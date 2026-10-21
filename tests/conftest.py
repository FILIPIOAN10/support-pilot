"""Shared fixtures. MongoDB and Redis are REAL, started in Docker by Testcontainers (or taken from TEST_MONGO_URL /
TEST_REDIS_URL, which is how CI points at its service containers). The model is the offline scripted one."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from supportpilot.agent import build_agent
from supportpilot.config import Settings
from supportpilot.fake import ScriptedSupportModel
from supportpilot.resilience import Conflict, ServiceUnavailable
from supportpilot.service import SupportService
from supportpilot.store import Store
from supportpilot.tools import build_tools


@pytest.fixture(scope="session")
def mongo_url():
    url = os.environ.get("TEST_MONGO_URL")
    if url:
        yield url
        return
    from testcontainers.community.mongodb import MongoDbContainer

    with MongoDbContainer("mongo:8") as mongo:
        yield mongo.get_connection_url()


@pytest.fixture(scope="session")
def redis_url():
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        yield url
        return
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture
def mongo(mongo_url):
    client = MongoClient(mongo_url, tz_aware=True)
    db = client[f"test_{uuid.uuid4().hex[:8]}"]  # a fresh database per test: nothing leaks between tests
    yield db
    client.drop_database(db.name)
    client.close()


@pytest.fixture
def redis_client(redis_url):
    client = redis.Redis.from_url(redis_url, socket_timeout=1)
    client.flushdb()
    yield client
    client.close()


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
        self.contexts = []  # the customer / conversation / approver sent with each refund call
        self.refuse = None  # set to a reason to make the order system answer 409
        self.down = False

    def get_order(self, order_id):
        if self.down:
            raise ServiceUnavailable("orders is unavailable")
        return self.orders.get(order_id)

    def create_refund(self, order_id, reason, idempotency_key, **context):
        if self.down:
            raise ServiceUnavailable("orders is unavailable")
        if self.refuse:
            raise Conflict(self.refuse)
        self.contexts.append(context)
        if idempotency_key not in self.refunds:
            self.refunds[idempotency_key] = {"refund_id": f"RF-{len(self.refunds) + 1:04d}", "status": "pending"}
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


@pytest.fixture
def make_service(mongo, settings, tools):
    """Builds a service over the real Mongo database. Call it twice to simulate a restart of the process."""

    def build(model=None, tools_=None):
        saver = MongoDBSaver(mongo.client, db_name=mongo.name)
        store = Store(mongo)
        store.ensure_indexes()
        agent = build_agent(model or ScriptedSupportModel(), tools_ or tools, saver, settings)
        return SupportService(agent, store), store

    return build
