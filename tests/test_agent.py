"""The agent loop with the scripted model and an in-memory checkpointer: no containers needed."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from supportpilot.agent import build_agent
from supportpilot.fake import ScriptedSupportModel, _call


def make(model, tools, settings):
    return build_agent(model, tools, InMemorySaver(), settings)


def cfg(thread="t1", customer="c-100"):
    return {"configurable": {"thread_id": thread, "customer_id": customer}}


def say(agent, text, **kw):
    return agent.invoke({"messages": [HumanMessage(text)]}, cfg(**kw), version="v2")


def called(result_or_state) -> list[str]:
    messages = result_or_state.value["messages"] if hasattr(result_or_state, "value") else result_or_state
    return [tc["name"] for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls]


def last_text(result) -> str:
    return result.value["messages"][-1].content


def test_an_order_question_calls_get_order_and_answers_from_its_result(tools, settings):
    result = say(make(ScriptedSupportModel(), tools, settings), "Where is my order 1002?")
    assert called(result) == ["get_order"] and not result.interrupts
    assert "shipped" in last_text(result) and "FastCourier" in last_text(result)


def test_a_policy_question_calls_search_policies(tools, settings):
    result = say(make(ScriptedSupportModel(), tools, settings), "How do returns work?")
    assert called(result) == ["search_policies"]
    assert "30 days" in last_text(result)


def test_a_refund_runs_two_checks_then_stops_for_a_human_and_creates_nothing_yet(tools, settings, orders):
    agent = make(ScriptedSupportModel(), tools, settings)
    result = say(agent, "I want a refund for order 1001, the shoes are too small", thread="r1")
    assert called(result) == ["check_refund_eligibility", "request_refund"]
    request = result.interrupts[0].value["action_requests"][0]
    assert request["name"] == "request_refund" and request["args"]["order_id"] == "1001"
    assert orders.refunds == {}  # the paused tool has NOT run


def test_approval_resumes_the_same_run_and_the_refund_is_created(tools, settings, orders):
    agent = make(ScriptedSupportModel(), tools, settings)
    say(agent, "I want a refund for order 1001", thread="r2")
    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg("r2"), version="v2")
    assert not result.interrupts and "RF-0001" in last_text(result)
    assert list(orders.refunds) == ["r2:1001"]


def test_rejection_means_no_refund_and_the_customer_is_told(tools, settings, orders):
    agent = make(ScriptedSupportModel(), tools, settings)
    say(agent, "I want a refund for order 1001", thread="r3")
    reject = {"decisions": [{"type": "reject", "message": "Used item."}]}
    result = agent.invoke(Command(resume=reject), cfg("r3"), version="v2")
    assert orders.refunds == {} and "Used item." in last_text(result)


def test_an_ineligible_refund_never_reaches_a_human(tools, settings, orders):
    result = say(
        make(ScriptedSupportModel(), tools, settings),
        "refund order 1003 please",
        customer="c-200",
    )
    assert called(result) == ["check_refund_eligibility"] and not result.interrupts
    assert "cannot be refunded" in last_text(result) and orders.refunds == {}


class Rogue(ScriptedSupportModel):
    """A model that has been talked into refunding somebody else's order."""

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            return super()._generate(messages, stop, run_manager, **kw)
        return ChatResult(generations=[ChatGeneration(message=_call("request_refund", order_id="1003", reason="x"))])


def test_even_a_compromised_model_cannot_refund_another_customers_order(tools, settings, orders):
    agent = make(Rogue(), tools, settings)
    say(agent, "hello", thread="rogue", customer="c-100")
    agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        cfg("rogue", "c-100"),
        version="v2",
    )
    assert orders.refunds == {}  # the human approved, and the tool still said no: the rules live in the tool


class Looping(ScriptedSupportModel):
    calls: list[int] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.calls.append(1)
        return ChatResult(generations=[ChatGeneration(message=_call("get_order", order_id="1001"))])


def test_a_model_stuck_in_a_loop_is_stopped_by_the_call_limits(tools, settings):
    model = Looping()
    result = say(make(model, tools, settings), "hello")
    assert len(model.calls) <= settings.max_model_calls
    assert len(called(result)) <= settings.max_tool_calls + 1


class Recording(ScriptedSupportModel):
    seen: list[Any] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.extend(str(m.content) for m in messages)
        return super()._generate(messages, stop, run_manager, **kw)


def test_email_addresses_are_redacted_before_the_model_sees_them(tools, settings):
    model = Recording()
    say(
        make(model, tools, settings),
        "My email is ana@example.com, where is order 1002?",
    )
    joined = "\n".join(model.seen)
    assert "ana@example.com" not in joined and "[REDACTED_EMAIL]" in joined


def test_state_is_kept_per_thread(tools, settings):
    agent = make(ScriptedSupportModel(), tools, settings)
    say(agent, "Where is my order 1002?", thread="a")
    say(agent, "How do returns work?", thread="a")
    say(agent, "How do returns work?", thread="b")
    assert len(agent.get_state(cfg("a")).values["messages"]) > len(agent.get_state(cfg("b")).values["messages"])
