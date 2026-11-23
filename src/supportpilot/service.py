"""The use cases: chat with the agent, decide a pending refund, read a conversation. No HTTP in here."""

import contextvars
import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import Command, GraphOutput
from openinference.instrumentation import using_attributes

from supportpilot.store import Store
from supportpilot.tools import HANDOFF_TOOL

log = logging.getLogger(__name__)

AWAITING_REPLY = (
    "I have sent your refund request to a colleague for approval. You will see the result in this conversation."
)


class ConversationNotFound(Exception):
    pass


class ApprovalPending(Exception):
    """The conversation is paused until a human decides; new messages must wait."""


class NothingToApprove(Exception):
    pass


class HandedOff(Exception):
    """A colleague has taken the conversation over; the agent stays out until they reply."""


COLLEAGUE = "support_colleague"  # the name on a human reply inside the transcript (model APIs accept [A-Za-z0-9_-])


@dataclass(frozen=True)
class Outcome:
    conversation_id: str
    status: str  # open | awaiting_approval | handed_off
    reply: str
    usage: dict[str, Any] | None = None  # what this turn cost: model calls, tool calls, tokens, seconds


def text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _tokens(usage_by_model: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage_by_model.values()),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage_by_model.values()),
    }


def _this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The messages produced since the customer's last message."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[last_human + 1 :]


def _events(part: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Translate LangGraph stream parts into the two kinds of live events the page shows."""
    if part["type"] == "messages":  # pieces of what the model writes, as it writes them
        message, meta = part["data"]
        if isinstance(message, AIMessage) and meta.get("langgraph_node") == "model" and (text := text_of(message)):
            yield {"event": "token", "data": {"text": text}}
    elif part["type"] == "updates":  # a node finished; the model node's message carries its complete tool calls
        update = part["data"].get("model")
        for message in update.get("messages", []) if isinstance(update, dict) else []:
            for call in getattr(message, "tool_calls", []):
                yield {"event": "tool", "data": {"name": call["name"], "args": call["args"]}}


class SupportService:
    def __init__(self, agent: Any, store: Store):
        self._agent = agent
        self._store = store

    @staticmethod
    def _config(conversation_id: str, customer_id: str) -> dict[str, Any]:
        # thread_id = the conversation: the checkpointer stores and restores the agent's state under it.
        # customer_id is what the tools read - it comes from the authenticated request, never from the model.
        return {
            "configurable": {"thread_id": conversation_id, "customer_id": customer_id},
            "recursion_limit": 30,
        }

    def _open(self, customer_id: str, conversation_id: str | None) -> str:
        """A new conversation, or an existing one that belongs to this customer and is not waiting for a human."""
        if conversation_id is None:
            return self._store.new_conversation(customer_id)
        conversation = self._store.get_conversation(conversation_id)
        # someone else's conversation looks exactly like one that does not exist
        if conversation is None or conversation["customer_id"] != customer_id:
            raise ConversationNotFound(conversation_id)
        if conversation["status"] == "awaiting_approval":
            raise ApprovalPending(conversation_id)
        if conversation["status"] == "handed_off":
            raise HandedOff(conversation_id)
        return conversation_id

    def chat(self, customer_id: str, message: str, conversation_id: str | None = None) -> Outcome:
        conversation_id = self._open(customer_id, conversation_id)
        started = time.perf_counter()
        # session = the conversation: the tracing UI groups every turn of one conversation together
        with get_usage_metadata_callback() as usage, using_attributes(session_id=conversation_id, user_id=customer_id):
            result = self._agent.invoke(
                {"messages": [HumanMessage(message)]},
                self._config(conversation_id, customer_id),
                version="v2",
            )
        return self._after_run(conversation_id, customer_id, result, _tokens(usage.usage_metadata), started)

    def chat_stream(
        self, customer_id: str, message: str, conversation_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Like chat(), but returns events while the agent works: each tool it calls, the reply as it is written,
        and at the end the same outcome as chat(). The checks run NOW, before the first event, so a foreign or
        paused conversation still becomes a normal 404 / 409 instead of an error in the middle of a stream."""
        conversation_id = self._open(customer_id, conversation_id)
        return self._stream(conversation_id, customer_id, {"messages": [HumanMessage(message)]})

    def _stream(self, conversation_id: str, customer_id: str, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        config = self._config(conversation_id, customer_id)

        def work() -> None:
            started = time.perf_counter()
            try:
                with (
                    get_usage_metadata_callback() as usage,
                    using_attributes(session_id=conversation_id, user_id=customer_id),
                ):
                    for part in self._agent.stream(payload, config, stream_mode=["messages", "updates"], version="v2"):
                        for event in _events(part):
                            events.put(event)
                state = self._agent.get_state(config)  # the checkpoint knows how the run ended: answer or pause
                result = GraphOutput(value=state.values, interrupts=state.interrupts)
                outcome = self._after_run(conversation_id, customer_id, result, _tokens(usage.usage_metadata), started)
                events.put({"event": "done", "data": asdict(outcome)})
            except Exception:
                log.exception("streamed run failed conversation=%s", conversation_id)
                events.put({"event": "error", "data": {"detail": "Internal error"}})
            finally:
                events.put(None)

        # The run gets its own thread: it finishes and is saved even if the browser disconnects halfway, and the
        # copied context keeps the request id in its log lines.
        threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True).start()
        yield {"event": "start", "data": {"conversation_id": conversation_id}}
        while (event := events.get()) is not None:
            yield event

    def decide(self, conversation_id: str, decision: str, note: str | None, admin: str) -> Outcome:
        claim = self._store.claim_approval(conversation_id)
        if claim is None:
            raise NothingToApprove(conversation_id)
        customer_id = claim["customer_id"]
        if decision == "approve":
            resume: dict[str, Any] = {"decisions": [{"type": "approve"}]}
        else:
            resume = {
                "decisions": [
                    {
                        "type": "reject",
                        "message": note or "The refund was not approved.",
                    }
                ]
            }
        config = self._config(conversation_id, customer_id)
        config["configurable"]["approved_by"] = admin  # read by request_refund, recorded by the order system
        started = time.perf_counter()
        try:
            with (
                get_usage_metadata_callback() as usage,
                using_attributes(session_id=conversation_id, user_id=customer_id, metadata={"approved_by": admin}),
            ):
                result = self._agent.invoke(Command(resume=resume), config, version="v2")
        except Exception:
            self._store.release_approval(conversation_id)  # so the decision can be retried instead of being stuck
            raise
        self._store.finish_approval(conversation_id, decision, note, admin)
        log.info(
            "approval conversation=%s decision=%s by=%s",
            conversation_id,
            decision,
            admin,
        )
        return self._after_run(conversation_id, customer_id, result, _tokens(usage.usage_metadata), started)

    def reply_as_colleague(self, conversation_id: str, message: str, admin: str) -> Outcome:
        """A person answers a handed-off conversation. The reply goes into the agent's own transcript, so when the
        customer writes again the agent sees what the colleague said and the conversation simply continues."""
        claim = self._store.claim_handoff(conversation_id, admin)
        if claim is None:
            raise NothingToApprove(conversation_id)
        config = self._config(conversation_id, claim["customer_id"])
        self._agent.update_state(config, {"messages": [AIMessage(message, name=COLLEAGUE)]})
        self._store.touch(conversation_id, "open")
        log.info("handoff answered conversation=%s by=%s", conversation_id, admin)
        return Outcome(conversation_id, "open", message)

    def _after_run(
        self, conversation_id: str, customer_id: str, result: Any, usage: dict[str, int], started: float
    ) -> Outcome:
        turn = _this_turn(result.value["messages"])
        calls = [call for m in turn if isinstance(m, AIMessage) for call in m.tool_calls]
        stats = {
            "model_calls": sum(isinstance(m, AIMessage) for m in turn),
            "tool_calls": len(calls),
            **usage,
            "seconds": round(time.perf_counter() - started, 2),
        }
        if result.interrupts:
            actions = result.interrupts[0].value["action_requests"]
            self._store.open_approval(conversation_id, customer_id, actions)
            self._store.touch(conversation_id, "awaiting_approval", usage)
            return Outcome(conversation_id, "awaiting_approval", AWAITING_REPLY, stats)
        reply = text_of(result.value["messages"][-1])
        handoff = next((c for c in calls if c["name"] == HANDOFF_TOOL), None)
        if handoff is not None:
            self._store.open_handoff(conversation_id, customer_id, str(handoff["args"].get("reason", ""))[:300])
            self._store.touch(conversation_id, "handed_off", usage)
            return Outcome(conversation_id, "handed_off", reply, stats)
        self._store.touch(conversation_id, "open", usage)
        return Outcome(conversation_id, "open", reply, stats)

    def pending_handoffs(self) -> list[dict[str, Any]]:
        """Each conversation waiting for a person, with its visible messages so the colleague has the context."""
        return [
            h | {"messages": self.messages(h["conversation_id"], h["customer_id"])[1]}
            for h in self._store.pending_handoffs()
        ]

    def messages(self, conversation_id: str, customer_id: str) -> tuple[str, list[dict[str, str]]]:
        conversation = self._store.get_conversation(conversation_id)
        if conversation is None or conversation["customer_id"] != customer_id:
            raise ConversationNotFound(conversation_id)
        state = self._agent.get_state(self._config(conversation_id, customer_id))
        visible = []
        for m in state.values.get("messages", []):
            text = text_of(m).strip()
            if isinstance(m, HumanMessage) and text:
                visible.append({"role": "customer", "content": text})
            elif isinstance(m, AIMessage) and text:  # tool calls and tool results are internal
                visible.append({"role": "colleague" if m.name == COLLEAGUE else "assistant", "content": text})
        return conversation["status"], visible

    def erase_customer(self, customer_id: str) -> int:
        """GDPR Art. 17: every conversation of this customer - the agent's checkpoints (the transcript itself),
        the conversation records and the approvals. Idempotent: erasing an erased customer erases nothing."""
        conversation_ids = self._store.conversation_ids(customer_id)
        for conversation_id in conversation_ids:
            self._agent.checkpointer.delete_thread(conversation_id)
        self._store.delete_customer(customer_id)
        log.info("erased customer=%s conversations=%d", customer_id, len(conversation_ids))
        return len(conversation_ids)
