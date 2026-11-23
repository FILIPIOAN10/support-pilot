"""The use cases against a real MongoDB (conversation records + LangGraph checkpoints)."""

import threading

import pytest
from langchain_core.outputs import ChatResult

from supportpilot.fake import ScriptedSupportModel
from supportpilot.service import ApprovalPending, ConversationNotFound, HandedOff, NothingToApprove


def test_a_simple_question_creates_a_conversation_and_records_usage(make_service):
    service, store = make_service()
    out = service.chat("c-100", "Where is my order 1002?")
    assert out.status == "open" and "shipped" in out.reply
    doc = store.get_conversation(out.conversation_id)
    assert doc["customer_id"] == "c-100" and doc["usage"]["input_tokens"] > 0 and doc["usage"]["output_tokens"] > 0


def test_a_conversation_can_be_continued_and_read_back_without_tool_internals(
    make_service,
):
    service, _ = make_service()
    first = service.chat("c-100", "Where is my order 1002?")
    service.chat("c-100", "How do returns work?", first.conversation_id)
    status, messages = service.messages(first.conversation_id, "c-100")
    assert status == "open"
    assert [m["role"] for m in messages] == [
        "customer",
        "assistant",
        "customer",
        "assistant",
    ]
    assert "internal_note" not in str(messages)


def test_someone_elses_conversation_is_invisible(make_service):
    service, _ = make_service()
    out = service.chat("c-100", "hello")
    with pytest.raises(ConversationNotFound):
        service.chat("c-200", "hi", out.conversation_id)
    with pytest.raises(ConversationNotFound):
        service.messages(out.conversation_id, "c-200")


def test_a_refund_pauses_the_conversation_and_survives_a_restart(make_service, orders):
    service, store = make_service()
    out = service.chat("c-100", "I want a refund for order 1001")
    assert out.status == "awaiting_approval" and orders.refunds == {}
    assert store.pending_approvals()[0]["actions"][0]["args"]["order_id"] == "1001"
    with pytest.raises(ApprovalPending):
        service.chat("c-100", "any news?", out.conversation_id)

    restarted, _ = make_service()  # a NEW process: new agent, new checkpointer, same database
    done = restarted.decide(out.conversation_id, "approve", None, "maria")
    assert done.status == "open" and "RF-0001" in done.reply
    assert list(orders.refunds) == [f"{out.conversation_id}:1001"]
    _, messages = restarted.messages(out.conversation_id, "c-100")
    assert "RF-0001" in messages[-1]["content"]


def test_a_decision_can_only_be_made_once(make_service, orders):
    service, store = make_service()
    out = service.chat("c-100", "refund order 1001")
    service.decide(out.conversation_id, "reject", "Used item", "maria")
    with pytest.raises(NothingToApprove):
        service.decide(out.conversation_id, "approve", None, "john")
    assert orders.refunds == {}
    assert store.approvals.find_one()["decided_by"] == "maria"


def test_two_admins_clicking_at_the_same_moment_create_exactly_one_refund(make_service, orders):
    service, _ = make_service()
    out = service.chat("c-100", "refund order 1001")
    barrier, results = threading.Barrier(2), []

    def admin(name):
        barrier.wait()
        try:
            service.decide(out.conversation_id, "approve", None, name)
            results.append("ok")
        except NothingToApprove:
            results.append("too late")

    threads = [threading.Thread(target=admin, args=(n,)) for n in ("maria", "john")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["ok", "too late"] and len(orders.refunds) == 1


class Exploding(ScriptedSupportModel):
    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        raise RuntimeError("model provider is down")


def test_a_failed_resume_puts_the_approval_back_so_it_can_be_retried(make_service, orders):
    service, store = make_service()
    out = service.chat("c-100", "refund order 1001")
    broken, _ = make_service(model=Exploding())
    with pytest.raises(RuntimeError):
        broken.decide(out.conversation_id, "approve", None, "maria")
    assert store.approvals.find_one()["status"] == "pending"  # not stuck in "deciding"
    good = service.decide(out.conversation_id, "approve", None, "maria")
    assert "RF-" in good.reply


def test_the_approving_admin_reaches_the_order_system(make_service, orders):
    service, _ = make_service()
    out = service.chat("c-100", "I want a refund for order 1001")
    service.decide(out.conversation_id, "approve", None, "maria")
    assert orders.contexts == [{"customer_id": "c-100", "conversation_id": out.conversation_id, "approved_by": "maria"}]


def test_erasing_a_customer_removes_transcripts_records_and_approvals_and_nobody_elses(make_service, mongo):
    service, store = make_service()
    mine = service.chat("c-100", "I want a refund for order 1001")  # paused: an approval exists too
    theirs = service.chat("c-200", "Where is my order 1004?")

    assert service.erase_customer("c-100") == 1
    assert store.get_conversation(mine.conversation_id) is None
    assert store.pending_approvals() == []
    assert mongo["checkpoints"].count_documents({"thread_id": mine.conversation_id}) == 0
    assert mongo["checkpoints"].count_documents({"thread_id": theirs.conversation_id}) > 0
    assert service.messages(theirs.conversation_id, "c-200")[1]
    assert service.erase_customer("c-100") == 0  # idempotent


def test_asking_for_a_person_hands_the_conversation_to_a_colleague(make_service):
    service, store = make_service()
    out = service.chat("c-100", "I want to talk to a human, please")
    assert out.status == "handed_off" and "colleague" in out.reply
    assert store.pending_handoffs()[0]["conversation_id"] == out.conversation_id
    with pytest.raises(HandedOff):  # the agent stays out until the colleague replies
        service.chat("c-100", "hello?", out.conversation_id)


def test_the_colleagues_reply_lands_in_the_transcript_and_the_agent_continues(make_service):
    service, store = make_service()
    out = service.chat("c-100", "Can I speak to a real agent?")
    waiting = service.pending_handoffs()
    assert [m["role"] for m in waiting[0]["messages"]] == ["customer", "assistant"]  # the colleague sees the context

    service.reply_as_colleague(out.conversation_id, "Hi, Maria here. How can I help?", "maria")
    with pytest.raises(NothingToApprove):  # a second colleague is too late
        service.reply_as_colleague(out.conversation_id, "Hi!", "john")
    assert store.pending_handoffs() == []

    later = service.chat("c-100", "Where is my order 1002?", out.conversation_id)  # back to the agent
    assert later.status == "open" and "shipped" in later.reply
    _, messages = service.messages(out.conversation_id, "c-100")
    assert ("colleague", "Hi, Maria here. How can I help?") in [(m["role"], m["content"]) for m in messages]


def test_every_turn_reports_what_it_cost(make_service):
    service, _ = make_service()
    usage = service.chat("c-100", "Where is my order 1002?").usage
    assert usage["model_calls"] == 2 and usage["tool_calls"] == 1  # decide on the tool, then answer
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0 and usage["seconds"] >= 0
