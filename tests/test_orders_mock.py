import pytest
from fastapi.testclient import TestClient

from supportpilot import orders_mock

KEY = {"X-Api-Key": "dev-orders-key"}


@pytest.fixture
def client():
    orders_mock.mode["value"] = "ok"
    orders_mock.REFUNDS.clear()
    return TestClient(orders_mock.app)


def test_the_key_is_required(client):
    assert client.get("/orders/1001").status_code == 401


def test_known_and_unknown_orders(client):
    assert client.get("/orders/1001", headers=KEY).json()["status"] == "delivered"
    assert client.get("/orders/9999", headers=KEY).status_code == 404


def test_the_same_idempotency_key_returns_the_same_refund(client):
    one = client.post(
        "/orders/1001/refunds",
        json={"reason": "x"},
        headers={**KEY, "Idempotency-Key": "k1"},
    ).json()
    again = client.post(
        "/orders/1001/refunds",
        json={"reason": "x"},
        headers={**KEY, "Idempotency-Key": "k1"},
    ).json()
    other = client.post(
        "/orders/1001/refunds",
        json={"reason": "x"},
        headers={**KEY, "Idempotency-Key": "k2"},
    ).json()
    assert one == again and other["refund_id"] != one["refund_id"]


def test_chaos_mode_makes_the_service_fail_on_demand(client):
    client.post("/chaos", json={"mode": "fail"})
    assert client.get("/orders/1001", headers=KEY).status_code == 503
    client.post("/chaos", json={"mode": "ok"})
    assert client.get("/orders/1001", headers=KEY).status_code == 200
