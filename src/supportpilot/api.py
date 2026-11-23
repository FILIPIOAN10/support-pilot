"""HTTP layer. Thin: authenticate, rate-limit, validate, call the service, translate exceptions to status codes."""

import hmac
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from supportpilot.agent import build_agent
from supportpilot.clients import make_docuask_client, make_orders_client
from supportpilot.config import Settings, get_settings
from supportpilot.drafting import ReplyDrafter
from supportpilot.llm import make_chat_model, make_draft_model, make_fallback_model
from supportpilot.ratelimit import RateLimiter
from supportpilot.schemas import (
    ApprovalOut,
    ChatIn,
    ChatOut,
    ColleagueReplyIn,
    ConversationOut,
    DecisionIn,
    DraftOut,
    HandoffOut,
    MessageOut,
)
from supportpilot.service import (
    ApprovalPending,
    ConversationNotFound,
    HandedOff,
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
    docuask = make_docuask_client(s)
    tools = build_tools(make_orders_client(s), docuask)
    agent = build_agent(make_chat_model(s), tools, saver, s, fallback=make_fallback_model(s))
    return SupportService(agent, get_store(), ReplyDrafter(make_draft_model(s), docuask))


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


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def _rate_limit(limiter: RateLimiter, customer: str) -> None:
    allowed, retry_after = limiter.hit(customer)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many messages, slow down",
            headers={"Retry-After": str(retry_after)},
        )


@contextmanager
def _conversation_errors() -> Iterator[None]:
    try:
        yield
    except ConversationNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conversation") from e
    except ApprovalPending as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A human is reviewing the last request; wait for the result",
        ) from e
    except HandedOff as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A colleague has taken over this conversation and will answer here",
        ) from e


@router.post("/chat", response_model=ChatOut, tags=["chat"])
def chat(body: ChatIn, customer: Customer, service: Service, limiter: Limiter, response: Response) -> ChatOut:
    _rate_limit(limiter, customer)
    with _conversation_errors():
        outcome = service.chat(customer, body.message, body.conversation_id)
    return ChatOut(
        conversation_id=outcome.conversation_id,
        status=outcome.status,
        reply=outcome.reply,
        usage=outcome.usage,
    )


def _sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Server-Sent Events: plain text over one HTTP response, one `event:` + `data:` block per event."""
    for e in events:
        yield f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False)}\n\n"


@router.post("/chat/stream", tags=["chat"], response_class=StreamingResponse)
def chat_stream(body: ChatIn, customer: Customer, service: Service, limiter: Limiter) -> StreamingResponse:
    """Same as /chat, streamed: `start`, then `tool` / `token` events while the agent works, then `done`
    (the same fields as ChatOut) or `error`. Auth, rate limit, 404 and 409 happen before the stream starts."""
    _rate_limit(limiter, customer)
    with _conversation_errors():
        events = service.chat_stream(customer, body.message, body.conversation_id)
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # no proxy may hold the events back
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut, tags=["chat"])
def conversation(conversation_id: str, customer: Customer, service: Service) -> ConversationOut:
    with _conversation_errors():
        state, messages = service.messages(conversation_id, customer)
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


@router.get("/admin/handoffs", response_model=list[HandoffOut], tags=["admin"])
def pending_handoffs(_: Admin, service: Service) -> list[HandoffOut]:
    """Conversations the agent passed to a person, oldest first, each with its visible messages."""
    return [HandoffOut(**h) for h in service.pending_handoffs()]


@router.post("/admin/handoffs/{conversation_id}", response_model=ChatOut, tags=["admin"])
def reply_to_handoff(conversation_id: str, body: ColleagueReplyIn, admin: Admin, service: Service) -> ChatOut:
    try:
        outcome = service.reply_as_colleague(conversation_id, body.message, admin)
    except NothingToApprove as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nobody is waiting for a colleague there") from e
    return ChatOut(conversation_id=outcome.conversation_id, status=outcome.status, reply=outcome.reply)


@router.post("/admin/handoffs/{conversation_id}/draft", response_model=DraftOut, tags=["admin"])
def draft_for_handoff(conversation_id: str, admin: Admin, service: Service) -> DraftOut:
    """An AI-suggested reply built from the conversation and the shop's policies. Sends nothing to the customer."""
    try:
        draft = service.draft_for_colleague(conversation_id, admin)
    except NothingToApprove as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nobody is waiting for a colleague there") from e
    return DraftOut(draft=draft.text, sources=draft.sources, usage=draft.usage)


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
