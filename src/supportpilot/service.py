"""The use cases: chat with the agent, decide a pending refund, read a conversation. No HTTP in here."""

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import Command

from supportpilot.store import Store

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


@dataclass(frozen=True)
class Outcome:
    conversation_id: str
    status: str  # open | awaiting_approval
    reply: str


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

    def chat(self, customer_id: str, message: str, conversation_id: str | None = None) -> Outcome:
        if conversation_id is None:
            conversation_id = self._store.new_conversation(customer_id)
        else:
            conversation = self._store.get_conversation(conversation_id)
            # someone else's conversation looks exactly like one that does not exist
            if conversation is None or conversation["customer_id"] != customer_id:
                raise ConversationNotFound(conversation_id)
            if conversation["status"] == "awaiting_approval":
                raise ApprovalPending(conversation_id)
        with get_usage_metadata_callback() as usage:
            result = self._agent.invoke(
                {"messages": [HumanMessage(message)]},
                self._config(conversation_id, customer_id),
                version="v2",
            )
        return self._after_run(conversation_id, customer_id, result, _tokens(usage.usage_metadata))

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
        try:
            with get_usage_metadata_callback() as usage:
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
        return self._after_run(conversation_id, customer_id, result, _tokens(usage.usage_metadata))

    def _after_run(self, conversation_id: str, customer_id: str, result: Any, usage: dict[str, int]) -> Outcome:
        if result.interrupts:
            actions = result.interrupts[0].value["action_requests"]
            self._store.open_approval(conversation_id, customer_id, actions)
            self._store.touch(conversation_id, "awaiting_approval", usage)
            return Outcome(conversation_id, "awaiting_approval", AWAITING_REPLY)
        self._store.touch(conversation_id, "open", usage)
        return Outcome(conversation_id, "open", text_of(result.value["messages"][-1]))

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
                visible.append({"role": "assistant", "content": text})
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
