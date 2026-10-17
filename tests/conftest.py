"""Fixturi minime pentru Faza C și D: doar clienți falși, fără containere."""

from datetime import UTC, datetime, timedelta

import pytest

from supportpilot.config import Settings
from supportpilot.resilience import ServiceUnavailable
from supportpilot.tools import build_tools


@pytest.fixture
def settings():
    return Settings(_env_file=None, llm_provider="fake", max_model_calls=6, max_tool_calls=5)


# --- stand-ins for the two services the tools call ---------------------------------------------------------------
def make_order(order_id, customer, status="delivered", delivered_days_ago=5):
    now = datetime.now(UTC)
    return {
        "id": order_id,
        "customer_id": customer,
        "status": status,
        "items": ["Running shoes"],
        "total": 89.9,
        "currency": "EUR",
        "carrier": "FastCourier",
        "eta": None,
        "placed_at": (now - timedelta(days=9)).isoformat(),
        "delivered_at": (now - timedelta(days=delivered_days_ago)).isoformat() if status == "delivered" else None,
        "internal_note": "VIP customer",
    }


class FakeOrders:
    def __init__(self):
        self.orders = {
            "1001": make_order("1001", "c-100"),
            "1002": make_order("1002", "c-100", status="shipped"),
            "1003": make_order("1003", "c-200", delivered_days_ago=45),
            "1004": make_order("1004", "c-200"),
        }
        self.refunds = {}
        self.down = False

    def get_order(self, order_id):
        if self.down:
            raise ServiceUnavailable("orders is unavailable")
        return self.orders.get(order_id)

    def create_refund(self, order_id, reason, idempotency_key):
        if self.down:
            raise ServiceUnavailable("orders is unavailable")
        if idempotency_key not in self.refunds:
            self.refunds[idempotency_key] = {
                "refund_id": f"RF-{len(self.refunds) + 1:04d}",
                "status": "pending",
            }
        return self.refunds[idempotency_key]


class FakeDocuAsk:
    def __init__(self):
        self.down = False

    def ask(self, question):
        if self.down:
            raise ServiceUnavailable("docuask is unavailable")
        if "warranty" in question.lower():
            return {"refused": True, "answer": None, "citations": []}
        return {
            "refused": False,
            "answer": "Items can be returned within 30 days of delivery. [1]",
            "citations": [{"n": 1, "document": "returns.md", "section": "Returns > Window"}],
        }


@pytest.fixture
def orders():
    return FakeOrders()


@pytest.fixture
def docuask():
    return FakeDocuAsk()


@pytest.fixture
def tools(orders, docuask):
    return build_tools(orders, docuask)
