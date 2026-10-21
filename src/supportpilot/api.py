"""HTTP layer. Thin: authenticate, rate-limit, validate, call the service, translate exceptions to status codes."""

import hmac
import re
from functools import lru_cache
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from supportpilot.agent import build_agent
from supportpilot.clients import make_docuask_client, make_orders_client
from supportpilot.config import Settings, get_settings
from supportpilot.llm import make_chat_model
from supportpilot.ratelimit import RateLimiter
from supportpilot.schemas import (
    ApprovalOut,
    ChatIn,
    ChatOut,
    ConversationOut,
    DecisionIn,
    MessageOut,
)
from supportpilot.service import (
    ApprovalPending,
    ConversationNotFound,
    NothingToApprove,
    SupportService,
)
from supportpilot.store import Store
from supportpilot.tools import build_tools

CUSTOMER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@lru_cache
def get_mongo() -> MongoClient:
    s = get_settings()
    return MongoClient(s.mongo_url, serverSelectionTimeoutMS=3000, tz_aware=True)


@lru_cache
def get_redis() -> redis.Redis:
    # short timeouts: a Redis outage must cost milliseconds per request, not seconds
    return redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)


@lru_cache
def get_store() -> Store:
    store = Store(get_mongo()[get_settings().mongo_db])
    store.ensure_indexes()
    return store


@lru_cache
def get_service() -> SupportService:
    s = get_settings()
    saver = MongoDBSaver(get_mongo(), db_name=s.mongo_db)
    tools = build_tools(make_orders_client(s), make_docuask_client(s))
    return SupportService(build_agent(make_chat_model(s), tools, saver, s), get_store())


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis(), get_settings().rate_limit_per_minute)


def _same(supplied: str | None, expected: str) -> bool:
    return supplied is not None and hmac.compare_digest(supplied.encode(), expected.encode())


def customer_id(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
    x_customer_id: Annotated[str | None, Header()] = None,
) -> str:
    if not _same(x_api_key, settings.gateway_api_key.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
    if not x_customer_id or not CUSTOMER_ID.match(x_customer_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Customer-Id is required")
    return x_customer_id


def admin_user(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_key: Annotated[str | None, Header()] = None,
    x_admin_user: Annotated[str | None, Header()] = None,
) -> str:
    if not _same(x_admin_key, settings.admin_api_key.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid admin key")
    return (x_admin_user or "admin")[:64]


Customer = Annotated[str, Depends(customer_id)]
Admin = Annotated[str, Depends(admin_user)]
Service = Annotated[SupportService, Depends(get_service)]

router = APIRouter(prefix="/v1")
health = APIRouter(prefix="/health", tags=["health"])


@router.post("/chat", response_model=ChatOut, tags=["chat"])
def chat(
    body: ChatIn,
    customer: Customer,
    service: Service,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    response: Response,
) -> ChatOut:
    allowed, retry_after = limiter.hit(customer)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many messages, slow down",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        outcome = service.chat(customer, body.message, body.conversation_id)
    except ConversationNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conversation") from e
    except ApprovalPending as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A human is reviewing the last request; wait for the result",
        ) from e
    return ChatOut(
        conversation_id=outcome.conversation_id,
        status=outcome.status,
        reply=outcome.reply,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut, tags=["chat"])
def conversation(conversation_id: str, customer: Customer, service: Service) -> ConversationOut:
    try:
        state, messages = service.messages(conversation_id, customer)
    except ConversationNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conversation") from e
    return ConversationOut(
        conversation_id=conversation_id,
        status=state,
        messages=[MessageOut(**m) for m in messages],
    )


@router.get("/admin/approvals", response_model=list[ApprovalOut], tags=["admin"])
def pending_approvals(_: Admin, store: Annotated[Store, Depends(get_store)]) -> list[ApprovalOut]:
    return [
        ApprovalOut(
            conversation_id=a["conversation_id"],
            customer_id=a["customer_id"],
            requested_at=a["requested_at"],
            actions=[{"name": x["name"], "args": x["args"]} for x in a["actions"]],
        )
        for a in store.pending_approvals()
    ]


@router.post("/admin/approvals/{conversation_id}", response_model=ChatOut, tags=["admin"])
def decide(conversation_id: str, body: DecisionIn, admin: Admin, service: Service) -> ChatOut:
    try:
        outcome = service.decide(conversation_id, body.decision, body.note, admin)
    except NothingToApprove as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing is waiting for approval there") from e
    return ChatOut(
        conversation_id=outcome.conversation_id,
        status=outcome.status,
        reply=outcome.reply,
    )


@router.delete(
    "/admin/customers/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["admin"],
)
def erase_customer(customer_id: str, _: Admin, service: Service) -> None:
    """GDPR erasure, called by the shop after it erased the account. 204 whether or not anything was left."""
    if not CUSTOMER_ID.match(customer_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid customer id")
    service.erase_customer(customer_id)


@health.get("/live")
def live() -> dict[str, str]:
    return {"status": "up"}


@health.get("/ready")
def ready(
    mongo: Annotated[MongoClient, Depends(get_mongo)],
    cache: Annotated[redis.Redis, Depends(get_redis)],
) -> dict[str, str]:
    """Mongo is required (state lives there). Redis is optional: the limiter fails open, so it is reported, not required."""
    try:
        mongo.admin.command("ping")
    except Exception as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "mongodb not ready") from e
    try:
        cache.ping()
        redis_state = "up"
    except redis.RedisError:
        redis_state = "down"
    return {"status": "ready", "redis": redis_state}
