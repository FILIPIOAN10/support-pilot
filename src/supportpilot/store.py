"""MongoDB: the records around a conversation. (The messages themselves live in the LangGraph checkpoint, same database.)

Why a document store here: a conversation is one self-contained record that is read and written whole, its shape
changes as the agent grows (new fields, new tools) and there are no joins - the textbook fit for documents.
The relational database stays where integrity across tables matters (project 1: documents <-> chunks).
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, db: Database):
        self.conversations = db["conversations"]
        self.approvals = db["approvals"]

    def ensure_indexes(self) -> None:
        self.conversations.create_index([("customer_id", 1), ("updated_at", DESCENDING)])
        self.approvals.create_index([("status", 1), ("requested_at", 1)])
        self.approvals.create_index("conversation_id")

    # --- conversations -------------------------------------------------------------------------------------------
    def new_conversation(self, customer_id: str) -> str:
        conversation_id = uuid4().hex
        self.conversations.insert_one(
            {
                "_id": conversation_id,
                "customer_id": customer_id,
                "status": "open",
                "created_at": _now(),
                "updated_at": _now(),
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )
        return conversation_id

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.conversations.find_one({"_id": conversation_id})

    def touch(self, conversation_id: str, status: str, usage: dict[str, int] | None = None) -> None:
        update: dict[str, Any] = {"$set": {"status": status, "updated_at": _now()}}
        if usage:
            update["$inc"] = {f"usage.{k}": v for k, v in usage.items()}
        self.conversations.update_one({"_id": conversation_id}, update)

    # --- approvals ------------------------------------------------------------------------------------------------
    def open_approval(self, conversation_id: str, customer_id: str, actions: list[dict[str, Any]]) -> None:
        self.approvals.insert_one(
            {
                "conversation_id": conversation_id,
                "customer_id": customer_id,
                "actions": actions,
                "status": "pending",
                "requested_at": _now(),
            }
        )

    def pending_approvals(self) -> list[dict[str, Any]]:
        return list(self.approvals.find({"status": "pending"}).sort("requested_at", 1))

    def claim_approval(self, conversation_id: str) -> dict[str, Any] | None:
        """Atomic: of two admins clicking at once, exactly one gets the document, the other gets None."""
        return self.approvals.find_one_and_update(
            {"conversation_id": conversation_id, "status": "pending"},
            {"$set": {"status": "deciding", "claimed_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )

    def release_approval(self, conversation_id: str) -> None:
        """The decision could not be applied (agent error): put it back so it can be retried."""
        self.approvals.update_one(
            {"conversation_id": conversation_id, "status": "deciding"},
            {"$set": {"status": "pending"}},
        )

    def finish_approval(self, conversation_id: str, decision: str, note: str | None, admin: str) -> None:
        self.approvals.update_one(
            {"conversation_id": conversation_id, "status": "deciding"},
            {
                "$set": {
                    "status": "done",
                    "decision": decision,
                    "note": note,
                    "decided_by": admin,
                    "decided_at": _now(),
                }
            },
        )
