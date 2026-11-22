"""The API contract."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class TurnUsage(BaseModel):
    """What one turn cost - shown under each reply in the page."""

    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    seconds: float


class ChatOut(BaseModel):
    conversation_id: str
    status: Literal["open", "awaiting_approval", "handed_off"]
    reply: str
    usage: TurnUsage | None = None


class MessageOut(BaseModel):
    role: Literal["customer", "assistant", "colleague"]
    content: str


class ConversationOut(BaseModel):
    conversation_id: str
    status: str
    messages: list[MessageOut]


class ActionOut(BaseModel):
    name: str
    args: dict[str, Any]


class ApprovalOut(BaseModel):
    conversation_id: str
    customer_id: str
    requested_at: datetime
    actions: list[ActionOut]


class HandoffOut(BaseModel):
    conversation_id: str
    customer_id: str
    reason: str
    requested_at: datetime
    messages: list[MessageOut]


class ColleagueReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)
