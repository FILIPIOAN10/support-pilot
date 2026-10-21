import pytest
import redis
from fastapi.testclient import TestClient

from supportpilot.api import (
    get_mongo,
    get_rate_limiter,
    get_redis,
    get_service,
    get_store,
)
from supportpilot.config import get_settings
from supportpilot.main import app
from supportpilot.ratelimit import RateLimiter

ADMIN = {"X-Admin-Key": "dev-admin-key", "X-Admin-User": "maria"}


@pytest.fixture
def client(make_service, redis_client, settings, mongo):
    service, store = make_service()
    app.dependency_overrides.update(
        {
            get_settings: lambda: settings,
            get_service: lambda: service,
            get_store: lambda: store,
            get_rate_limiter: lambda: RateLimiter(redis_client, limit=5),
            get_mongo: lambda: mongo.client,
            get_redis: lambda: redis_client,
        }
    )
    c = TestClient(app)  # no `with`: the lifespan would build the real service (needs a real Mongo URL)
    c.headers.update({"X-API-Key": "dev-gateway-key", "X-Customer-Id": "c-100"})
    yield c
    app.dependency_overrides.clear()


def test_authentication_and_customer_identity_are_required(client):
    assert client.post("/v1/chat", json={"message": "hi"}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/v1/chat", json={"message": "hi"}, headers={"X-Customer-Id": ""}).status_code == 400
    assert client.post("/v1/chat", json={"message": "hi"}, headers={"X-Customer-Id": "../etc"}).status_code == 400


def test_chat_and_read_the_conversation_back(client):
    out = client.post("/v1/chat", json={"message": "Where is my order 1002?"}).json()
    assert out["status"] == "open" and "shipped" in out["reply"]
    convo = client.get(f"/v1/conversations/{out['conversation_id']}").json()
    assert [m["role"] for m in convo["messages"]] == ["customer", "assistant"]


def test_the_full_refund_story_over_http(client):
    out = client.post("/v1/chat", json={"message": "I want a refund for order 1001"}).json()
    cid = out["conversation_id"]
    assert out["status"] == "awaiting_approval"

    blocked = client.post("/v1/chat", json={"message": "hello?", "conversation_id": cid})
    assert blocked.status_code == 409

    assert client.get("/v1/admin/approvals").status_code == 401  # a customer key is not an admin key
    pending = client.get("/v1/admin/approvals", headers=ADMIN).json()
    assert pending[0]["conversation_id"] == cid and pending[0]["actions"][0]["args"]["order_id"] == "1001"

    done = client.post(f"/v1/admin/approvals/{cid}", json={"decision": "approve"}, headers=ADMIN).json()
    assert done["status"] == "open" and "RF-0001" in done["reply"]
    assert client.post(f"/v1/admin/approvals/{cid}", json={"decision": "approve"}, headers=ADMIN).status_code == 404
    assert client.get("/v1/admin/approvals", headers=ADMIN).json() == []
    assert "RF-0001" in client.get(f"/v1/conversations/{cid}").json()["messages"][-1]["content"]


def test_another_customer_gets_404_for_my_conversation(client):
    cid = client.post("/v1/chat", json={"message": "hello"}).json()["conversation_id"]
    other = {"X-Customer-Id": "c-200"}
    assert client.get(f"/v1/conversations/{cid}", headers=other).status_code == 404
    assert client.post("/v1/chat", json={"message": "hi", "conversation_id": cid}, headers=other).status_code == 404


def test_too_many_messages_get_429_with_retry_after(client):
    codes = [client.post("/v1/chat", json={"message": "hello"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    blocked = client.post("/v1/chat", json={"message": "hello"})
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60


def test_input_is_validated(client):
    assert client.post("/v1/chat", json={"message": ""}).status_code == 422
    assert client.post("/v1/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/v1/chat", json={}).status_code == 422


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "up"}
    assert client.get("/health/ready").json() == {"status": "ready", "redis": "up"}


def test_readiness_survives_a_redis_outage_because_the_limiter_fails_open(client):
    # built like the app builds its client (from_url): redis.Redis(...) would retry by default and take seconds
    dead = redis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
    app.dependency_overrides[get_redis] = lambda: dead
    assert client.get("/health/ready").json() == {"status": "ready", "redis": "down"}


def test_the_home_page_needs_no_key_and_is_safe_by_construction(client):
    client.headers.pop("X-API-Key")
    response = client.get("/")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "SupportPilot" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "innerHTML" not in response.text  # replies are only ever rendered with textContent


def test_the_shop_can_erase_a_customer_with_the_admin_key_only(client):
    cid = client.post("/v1/chat", json={"message": "Where is my order 1002?"}).json()["conversation_id"]
    assert client.delete("/v1/admin/customers/c-100").status_code == 401  # the gateway key is not enough
    assert client.delete("/v1/admin/customers/..%2Fx", headers=ADMIN).status_code in (400, 404)
    assert client.delete("/v1/admin/customers/c-100", headers=ADMIN).status_code == 204
    assert client.get(f"/v1/conversations/{cid}").status_code == 404
    assert client.delete("/v1/admin/customers/c-100", headers=ADMIN).status_code == 204  # idempotent
