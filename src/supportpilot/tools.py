"""The tools the agent may call. The model chooses WHICH tool and with WHAT arguments - so every argument is untrusted.

The one rule that matters: the customer's identity never comes from the model. It is read from the run configuration,
which the server filled in from the authenticated request. A tool that took `customer_id` as an argument would let a
prompt-injected model (or a curious user) read someone else's orders.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from supportpilot.clients import DocuAskClient, OrdersClient
from supportpilot.resilience import ServiceUnavailable

REFUND_WINDOW_DAYS = 30
UNAVAILABLE = "The system is unavailable right now. Tell the customer to try again in a few minutes."


def _customer(config: RunnableConfig) -> str:
    return config["configurable"]["customer_id"]


def _public(order: dict[str, Any]) -> dict[str, Any]:
    """Only what the customer may see. Internal fields (customer id, internal notes) stay out of the model's context."""
    keys = (
        "id",
        "status",
        "items",
        "total",
        "currency",
        "carrier",
        "eta",
        "placed_at",
        "delivered_at",
    )
    return {k: order[k] for k in keys if k in order}


def _eligibility(order: dict[str, Any], now: datetime) -> tuple[bool, str]:
    if order["status"] != "delivered":
        return (
            False,
            f"the order is '{order['status']}', only delivered orders can be refunded",
        )
    delivered = datetime.fromisoformat(order["delivered_at"])
    if now - delivered > timedelta(days=REFUND_WINDOW_DAYS):
        return False, f"it was delivered more than {REFUND_WINDOW_DAYS} days ago"
    return True, f"delivered within the last {REFUND_WINDOW_DAYS} days"


def build_tools(orders: OrdersClient, docuask: DocuAskClient, now=lambda: datetime.now(UTC)) -> list[BaseTool]:
    def own_order(order_id: str, config: RunnableConfig) -> dict[str, Any] | None:
        order = orders.get_order(order_id.strip())
        # same answer for "does not exist" and "belongs to someone else": do not confirm that an order number exists
        return order if order and order["customer_id"] == _customer(config) else None

    @tool
    def get_order(order_id: str, config: RunnableConfig) -> str:
        """Look up ONE order of the current customer by its number. Returns status, items, carrier and delivery estimate."""
        try:
            order = own_order(order_id, config)
        except ServiceUnavailable:
            return UNAVAILABLE
        return json.dumps(_public(order)) if order else f"No order {order_id} was found for this customer."

    @tool
    def search_policies(question: str) -> str:
        """Search the shop's written policies (returns, shipping, warranty, payment). Use it for any 'how does X work' question."""
        try:
            answer = docuask.ask(question)
        except ServiceUnavailable:
            return UNAVAILABLE
        if answer["refused"]:
            return "The policies do not cover this question. Do not guess; offer to pass it to a human agent."
        sources = ", ".join(f"{c['document']} ({c['section']})" for c in answer["citations"])
        return f"{answer['answer']}\nSources: {sources}"

    @tool
    def check_refund_eligibility(order_id: str, config: RunnableConfig) -> str:
        """Check whether an order of the current customer can be refunded. ALWAYS call this before request_refund."""
        try:
            order = own_order(order_id, config)
        except ServiceUnavailable:
            return UNAVAILABLE
        if order is None:
            return f"No order {order_id} was found for this customer."
        eligible, reason = _eligibility(order, now())
        return json.dumps({"order_id": order["id"], "eligible": eligible, "reason": reason})

    @tool
    def request_refund(order_id: str, reason: str, config: RunnableConfig) -> str:
        """Ask for a refund of a whole order. Only after check_refund_eligibility said it is eligible. A human approves it first."""
        try:
            order = own_order(order_id, config)
            if order is None or not _eligibility(order, now())[0]:
                return f"Order {order_id} cannot be refunded."  # re-checked: the state may have changed while waiting
            # one refund per order per conversation, even if the approval is submitted twice
            key = f"{config['configurable']['thread_id']}:{order['id']}"
            refund = orders.create_refund(order["id"], reason[:500], key)
        except ServiceUnavailable:
            return UNAVAILABLE
        return json.dumps({"refund_id": refund["refund_id"], "status": refund["status"]})

    return [get_order, search_policies, check_refund_eligibility, request_refund]
