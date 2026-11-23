"""Triage: the deterministic step in front of the agent. No containers needed."""

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from supportpilot.agent import build_agent
from supportpilot.fake import ScriptedSupportModel
from supportpilot.triage import REPLIES, TRIAGE, classify, messages_of


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("What's the weather like today?", "off_topic"),
        ("Tell me a joke", "off_topic"),
        ("Write me a poem about shoes", "off_topic"),
        ("Hi!", "greeting"),
        ("Bună ziua", "greeting"),
        ("Where is my order 1002?", "agent"),
        ("Can I pay with PayPal?", "agent"),
        ("Unde este comanda mea?", "agent"),
        ("Is the weather a reason my parcel is late?", "agent"),  # a support signal always wins
        ("I have a question", "agent"),  # unsure -> the agent decides
    ],
)
def test_classify(text, route):
    assert classify(text) == route


class NeverCalled(ScriptedSupportModel):
    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        raise AssertionError("the model must not be called for this message")


def cfg(thread="t1"):
    return {"configurable": {"thread_id": thread, "customer_id": "c-100"}}


def test_off_topic_and_greetings_are_answered_without_the_model(tools, settings):
    graph = build_agent(NeverCalled(), tools, InMemorySaver(), settings)
    for text, route in [("What's the weather like today?", "off_topic"), ("Hello", "greeting")]:
        reply = graph.invoke({"messages": [HumanMessage(text)]}, cfg(text), version="v2").value["messages"][-1]
        assert reply.content == REPLIES[route] and reply.name == TRIAGE


def test_support_questions_still_reach_the_agent_and_the_pause_works_through_the_workflow(tools, settings, orders):
    graph = build_agent(ScriptedSupportModel(), tools, InMemorySaver(), settings)
    result = graph.invoke({"messages": [HumanMessage("I want a refund for order 1001")]}, cfg("r"), version="v2")
    assert result.interrupts  # the agent subgraph paused; the outer graph reports it
    tools_called = [c["name"] for m in messages_of(graph, cfg("r"), result) for c in getattr(m, "tool_calls", [])]
    assert tools_called == ["check_refund_eligibility", "request_refund"]
    done = graph.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg("r"), version="v2")
    assert "RF-0001" in done.value["messages"][-1].content and list(orders.refunds) == ["r:1001"]


def test_the_graph_is_a_workflow_with_the_agent_inside(tools, settings):
    nodes = set(build_agent(ScriptedSupportModel(), tools, InMemorySaver(), settings).get_graph().nodes)
    assert {"triage", "agent", "answer_directly"} <= nodes
