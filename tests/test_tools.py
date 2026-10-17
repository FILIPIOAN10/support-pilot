import json

import pytest


def run(tools, name, args, customer="c-100", thread="t1"):
    tool = next(t for t in tools if t.name == name)
    return tool.invoke(args, config={"configurable": {"customer_id": customer, "thread_id": thread}})


def test_a_customer_sees_their_own_order_without_internal_fields(tools):
    data = json.loads(run(tools, "get_order", {"order_id": "1001"}))
    assert data["status"] == "delivered" and data["items"] == ["Running shoes"]
    assert "internal_note" not in data and "customer_id" not in data


def test_asking_for_someone_elses_order_looks_exactly_like_asking_for_a_missing_one(
    tools,
):
    other = run(tools, "get_order", {"order_id": "1003"}, customer="c-100")  # belongs to c-200
    missing = run(tools, "get_order", {"order_id": "9999"}, customer="c-100")
    assert other == "No order 1003 was found for this customer."
    assert other.replace("1003", "X") == missing.replace("9999", "X")


def test_the_customer_identity_is_not_an_argument_the_model_can_set(tools):
    schema = next(t for t in tools if t.name == "get_order").args
    assert list(schema) == ["order_id"]  # no customer_id, no config in what the model sees


@pytest.mark.parametrize(
    ("order_id", "eligible"),
    [("1001", True), ("1002", False)],  # delivered 5 days ago / still shipped
)
def test_refund_eligibility_rules(tools, order_id, eligible):
    assert json.loads(run(tools, "check_refund_eligibility", {"order_id": order_id}))["eligible"] is eligible


def test_an_order_delivered_45_days_ago_is_out_of_the_window(tools):
    data = json.loads(run(tools, "check_refund_eligibility", {"order_id": "1003"}, customer="c-200"))
    assert data["eligible"] is False and "30 days" in data["reason"]


def test_request_refund_rechecks_ownership_and_eligibility_itself(tools, orders):
    # a compromised or confused model calls the tool directly with someone else's order, or an ineligible one
    assert "cannot be refunded" in run(tools, "request_refund", {"order_id": "1003", "reason": "x"}, customer="c-100")
    assert "cannot be refunded" in run(tools, "request_refund", {"order_id": "1002", "reason": "x"})
    assert orders.refunds == {}


def test_refunding_twice_in_one_conversation_creates_one_refund(tools, orders):
    first = json.loads(
        run(
            tools,
            "request_refund",
            {"order_id": "1001", "reason": "too small"},
            thread="conv-1",
        )
    )
    second = json.loads(
        run(
            tools,
            "request_refund",
            {"order_id": "1001", "reason": "too small"},
            thread="conv-1",
        )
    )
    assert first == second and len(orders.refunds) == 1
    assert list(orders.refunds) == ["conv-1:1001"]


def test_policy_answers_carry_their_sources_and_refusals_say_so(tools):
    ok = run(tools, "search_policies", {"question": "How do returns work?"})
    assert "30 days" in ok and "returns.md (Returns > Window)" in ok
    assert "do not cover" in run(tools, "search_policies", {"question": "What is the warranty?"})


def test_when_a_service_is_down_the_model_gets_a_clear_message_not_an_exception(tools, orders, docuask):
    orders.down = docuask.down = True
    for name, args in [
        ("get_order", {"order_id": "1001"}),
        ("check_refund_eligibility", {"order_id": "1001"}),
        ("request_refund", {"order_id": "1001", "reason": "x"}),
        ("search_policies", {"question": "returns?"}),
    ]:
        assert "unavailable" in run(tools, name, args)
