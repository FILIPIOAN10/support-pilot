"""Triage: a deterministic first step in front of the agent, as an explicit LangGraph workflow.

    START -> triage -+-> agent (the tool-calling loop, a subgraph) -> END
                     +-> answer_directly (fixed text, no model)      -> END

Not every message needs an agent. A greeting or an obviously off-topic request ("tell me a joke") gets a fixed answer
in milliseconds, with zero tokens and zero tool calls. Everything else, including anything the rules are unsure about,
goes to the agent - so the rules can only save cost, never block a real support question.
Routing is plain code on purpose: it is free, instant, testable and cannot be talked out of its decision.
"""

import re
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

TRIAGE = "triage"  # the name on a direct reply, so it is never counted as a model call
DIRECT_NODE = "answer_directly"

# a support signal always wins: the agent gets the message
SUPPORT = re.compile(
    r"\b\d{4,6}\b|order|refund|return|ship|deliver|track|parcel|package|pay|invoice|warranty|money|cancel|"
    r"broken|damaged|discount|coupon|voucher|price|size|account|address|human|person|colleague|agent|comand|livr|retur|bani|garan|bestell|liefer|zahl|paket",
    re.IGNORECASE,
)
GREETING = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|bun[aă]( ziua)?|salut|hallo|guten tag)\s*[!.]*\s*$",
    re.IGNORECASE,
)
OFF_TOPIC = re.compile(
    r"weather|joke|poem|song|recipe|football|soccer|politic|news|crypto|bitcoin|horoscope|"
    r"homework|essay|write (me )?(a|an|some) |code|program|translate|capital of|who won",
    re.IGNORECASE,
)

REPLIES = {
    "greeting": "Hello! I can check an order, explain our policies (returns, shipping, warranty, payment), "
    "start a refund, or pass you to a colleague.",
    "off_topic": "I can only help with orders, refunds and the shop's policies, so I can't answer that one. "
    "Ask me about an order (with its number) or a policy, or ask for a colleague.",
}

Route = Literal["agent", "greeting", "off_topic"]


def classify(text: str) -> Route:
    if SUPPORT.search(text):
        return "agent"
    if GREETING.match(text):
        return "greeting"
    if OFF_TOPIC.search(text):
        return "off_topic"
    return "agent"  # unsure -> the agent decides


class SupportState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    route: Route


def triage(state: SupportState) -> dict[str, Any]:
    last = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    return {"route": classify(str(last.content)) if last else "agent"}


def answer_directly(state: SupportState) -> dict[str, Any]:
    return {"messages": [AIMessage(REPLIES[state["route"]], name=TRIAGE)]}


def after_triage(state: SupportState) -> str:
    return "agent" if state["route"] == "agent" else DIRECT_NODE


def build_support_graph(agent, checkpointer):
    """The workflow around the agent. The agent subgraph shares `messages` with it and inherits its checkpointer,
    so the approval pause, the restart and the resume work exactly as before."""
    graph = StateGraph(SupportState)
    graph.add_node(TRIAGE, triage)
    graph.add_node("agent", agent)
    graph.add_node(DIRECT_NODE, answer_directly)
    graph.add_edge(START, TRIAGE)
    graph.add_conditional_edges(TRIAGE, after_triage, ["agent", DIRECT_NODE])
    graph.add_edge("agent", END)
    graph.add_edge(DIRECT_NODE, END)
    return graph.compile(checkpointer=checkpointer)


def messages_of(graph, config: dict[str, Any], result: Any) -> list[AnyMessage]:
    """All messages of the run. While the agent waits for a human, its latest messages (the tool calls that led to
    the pause) are still inside the paused subgraph, not yet in the outer state - so read them from there."""
    if result.interrupts:
        for task in graph.get_state(config, subgraphs=True).tasks:
            if task.state is not None:
                return task.state.values["messages"]
    return result.value["messages"]
