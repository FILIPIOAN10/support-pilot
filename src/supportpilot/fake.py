"""An offline stand-in for the chat model that behaves like a (very simple) tool-calling agent.

It is a handful of regular expressions, not intelligence. What it gives you is DETERMINISM: the same message always
makes the same tool calls, so a test can assert the exact sequence "check eligibility -> request refund -> ask a human".
The real model decides differently, but it is held to the same rules by the same tools, limits and approval gate.
"""

import json
import re
import uuid
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ORDER_ID = re.compile(r"\b(\d{4,6})\b")
REFUND = re.compile(r"refund|money back|send it back", re.IGNORECASE)
STATUS = re.compile(r"where|status|track|deliver|arrive|shipped|when", re.IGNORECASE)
POLICY = re.compile(r"\?|policy|policies|warranty|shipping|return", re.IGNORECASE)
HUMAN = re.compile(r"\b(human|person|real agent|colleague|manager|talk to someone)\b", re.IGNORECASE)


def _call(name: str, **args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}],
    )


class ScriptedSupportModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-support"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedSupportModel":
        return self  # it already "knows" its tools

    def _react_to_tool(self, tool_message: ToolMessage, question: str) -> AIMessage:
        text = str(tool_message.content)
        match tool_message.name:
            case "check_refund_eligibility":
                data = json.loads(text) if text.startswith("{") else {}
                if data.get("eligible"):
                    return _call(
                        "request_refund",
                        order_id=data["order_id"],
                        reason=question[:300],
                    )
                if data:
                    return AIMessage(
                        content=f"I'm sorry, order {data['order_id']} cannot be refunded: {data['reason']}."
                    )
                return AIMessage(content=text)
            case "request_refund":
                data = json.loads(text) if text.startswith("{") else {}
                if "refund_id" in data:
                    return AIMessage(content=f"Done. Refund {data['refund_id']} is registered and {data['status']}.")
                return AIMessage(content=f"About your refund: {text}")
            case "handoff_to_human":
                return AIMessage(content="I'm passing you to a colleague, who will answer here shortly.")
            case "get_order":
                data = json.loads(text) if text.startswith("{") else {}
                if data:
                    eta = data.get("eta") or "not available"
                    return AIMessage(
                        content=f"Order {data['id']} is {data['status']} (carrier: {data.get('carrier')}, estimated delivery: {eta})."
                    )
                return AIMessage(content=text)
            case _:
                return AIMessage(content=text)

    def _react_to_human(self, text: str) -> AIMessage:
        if HUMAN.search(text):
            return _call("handoff_to_human", reason=text[:300])
        found = ORDER_ID.search(text)
        order_id = found.group(1) if found else None
        if order_id and REFUND.search(text):
            return _call("check_refund_eligibility", order_id=order_id)
        if order_id and STATUS.search(text):
            return _call("get_order", order_id=order_id)
        if POLICY.search(text):
            return _call("search_policies", question=text)
        return AIMessage(content="Hello! I can check an order, explain our policies, or start a refund request.")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return _result(self._decide(messages), messages, "scripted-support")

    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        humans = [m for m in messages if isinstance(m, HumanMessage)]
        question = str(humans[-1].content) if humans else ""
        last = messages[-1]
        return self._react_to_tool(last, question) if isinstance(last, ToolMessage) else self._react_to_human(question)


def _result(reply: AIMessage, messages: list[BaseMessage], name: str) -> ChatResult:
    """Wraps a reply with a rough token count (4 characters per token), so the cost line has numbers offline too."""
    n_in = sum(len(str(m.content)) for m in messages) // 4
    n_out = max(1, len(str(reply.content)) // 4)
    reply.usage_metadata = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
    reply.response_metadata = {"model_name": name}
    return ChatResult(generations=[ChatGeneration(message=reply)])


POLICY_EXCERPT = re.compile(r"POLICY EXCERPT:\n(.*?)\n\n", re.DOTALL)


class ScriptedDraftModel(BaseChatModel):
    """The offline stand-in for the colleague's draft: a greeting, the policy excerpt if there is one, a question."""

    @property
    def _llm_type(self) -> str:
        return "scripted-draft"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        found = POLICY_EXCERPT.search(str(messages[-1].content))
        policy = re.sub(r"\s*\[\d+\]", "", found.group(1)).strip() if found else "(none)"
        if policy == "(none)":
            text = "Hi, thanks for waiting! I'm a colleague from the support team. Could you tell me a bit more about what you need?"
        else:
            text = f"Hi, thanks for waiting! I'm a colleague from the support team. {policy} Is there anything else I can help with?"
        return _result(AIMessage(content=text), messages, "scripted-draft")
