"""A stand-in for the shop's order system, so the agent has a real HTTP service to call in development and in compose.

    uvicorn supportpilot.orders_mock:app --port 9000

POST /chaos {"mode": "fail"}   every request answers 503   (watch the retries, then the breaker open)
POST /chaos {"mode": "slow"}   every request takes 10 s    (watch the read timeout)
POST /chaos {"mode": "ok"}     back to normal
"""

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

API_KEY = os.environ.get("ORDERS_API_KEY", "dev-orders-key")
NOW = datetime.now(UTC)


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


ORDERS: dict[str, dict[str, Any]] = {
    "1001": {
        "id": "1001",
        "customer_id": "c-100",
        "status": "delivered",
        "items": ["Running shoes"],
        "total": 89.9,
        "currency": "EUR",
        "carrier": "FastCourier",
        "eta": None,
        "placed_at": _ago(9),
        "delivered_at": _ago(5),
    },
    "1002": {
        "id": "1002",
        "customer_id": "c-100",
        "status": "shipped",
        "items": ["Backpack", "Water bottle"],
        "total": 54.0,
        "currency": "EUR",
        "carrier": "FastCourier",
        "eta": (NOW + timedelta(days=2)).date().isoformat(),
        "placed_at": _ago(3),
        "delivered_at": None,
        "internal_note": "VIP customer, escalate delays",
    },
    "1003": {
        "id": "1003",
        "customer_id": "c-200",
        "status": "delivered",
        "items": ["Desk lamp"],
        "total": 32.5,
        "currency": "EUR",
        "carrier": "EuroPost",
        "eta": None,
        "placed_at": _ago(50),
        "delivered_at": _ago(45),
    },
    "1004": {
        "id": "1004",
        "customer_id": "c-200",
        "status": "placed",
        "items": ["Notebook"],
        "total": 7.9,
        "currency": "EUR",
        "carrier": None,
        "eta": None,
        "placed_at": _ago(1),
        "delivered_at": None,
    },
}
REFUNDS: dict[str, dict[str, Any]] = {}  # idempotency key -> refund
mode = {"value": "ok"}

app = FastAPI(title="Orders (mock)")


def authenticate(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(401, "bad key")


def chaos() -> None:
    if mode["value"] == "fail":
        raise HTTPException(503, "orders system is down")
    if mode["value"] == "slow":
        time.sleep(10)


class Chaos(BaseModel):
    mode: str


class RefundIn(BaseModel):
    reason: str


@app.post("/chaos")
def set_chaos(body: Chaos) -> dict[str, str]:
    mode["value"] = body.mode
    return {"mode": body.mode}


@app.get("/orders/{order_id}", dependencies=[Depends(authenticate), Depends(chaos)])
def get_order(order_id: str) -> dict[str, Any]:
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(404, "no such order")
    return order


@app.post(
    "/orders/{order_id}/refunds",
    status_code=201,
    dependencies=[Depends(authenticate), Depends(chaos)],
)
def create_refund(order_id: str, body: RefundIn, idempotency_key: Annotated[str, Header()]) -> dict[str, Any]:
    if order_id not in ORDERS:
        raise HTTPException(404, "no such order")
    # the same key returns the same refund: a retried or duplicated request never refunds twice
    if idempotency_key not in REFUNDS:
        REFUNDS[idempotency_key] = {
            "refund_id": f"RF-{len(REFUNDS) + 1:04d}",
            "order_id": order_id,
            "status": "pending",
        }
    return REFUNDS[idempotency_key]


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "up"}
