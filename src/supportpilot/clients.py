"""The two services the agent calls. Each client hides the HTTP details behind the few methods the tools need."""

from typing import Any

from supportpilot.config import Settings
from supportpilot.resilience import CircuitBreaker, NotFound, ResilientHttp


class OrdersClient:
    """The shop's order system (a separate service we do not own)."""

    def __init__(self, http: ResilientHttp):
        self._http = http

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """None when the order does not exist. ServiceUnavailable when the order system is down."""
        try:
            return self._http.request("GET", f"/orders/{order_id}")
        except NotFound:
            return None

    def create_refund(
        self,
        order_id: str,
        reason: str,
        idempotency_key: str,
        *,
        customer_id: str | None = None,
        conversation_id: str | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Safe to retry (and to call twice): the same key returns the same refund instead of creating a second one.

        The context headers let the order system re-check ownership itself and record who approved the refund.
        Raises Conflict when the order system refuses (not delivered, outside the window, a return already open)."""
        context = {"X-Customer-Id": customer_id, "X-Conversation-Id": conversation_id, "X-Approved-By": approved_by}
        return self._http.request(
            "POST",
            f"/orders/{order_id}/refunds",
            json={"reason": reason},
            headers={"Idempotency-Key": idempotency_key} | {k: v for k, v in context.items() if v},
        )


class DocuAskClient:
    """The RAG service from project 1: the company's policies, answered with citations."""

    def __init__(self, http: ResilientHttp):
        self._http = http

    def ask(self, question: str) -> dict[str, Any]:
        return self._http.request("POST", "/v1/ask", json={"question": question})


def make_orders_client(s: Settings) -> OrdersClient:
    http = ResilientHttp(
        "orders",
        s.orders_base_url,
        {"X-Api-Key": s.orders_api_key.get_secret_value()},
        connect_timeout=s.http_connect_timeout,
        read_timeout=s.http_read_timeout,
        retries=s.http_retries,
        breaker=CircuitBreaker(s.breaker_failures, s.breaker_reset_seconds),
    )
    return OrdersClient(http)


def make_docuask_client(s: Settings) -> DocuAskClient:
    http = ResilientHttp(
        "docuask",
        s.docuask_base_url,
        {"X-API-Key": s.docuask_api_key.get_secret_value()},
        connect_timeout=s.http_connect_timeout,
        read_timeout=s.http_read_timeout + 20,  # an LLM answer is slow; a lookup is not
        retries=2,
        breaker=CircuitBreaker(s.breaker_failures, s.breaker_reset_seconds),
    )
    return DocuAskClient(http)
