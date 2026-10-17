import httpx
import pytest

from supportpilot.resilience import (
    CircuitBreaker,
    NotFound,
    ResilientHttp,
    ServiceUnavailable,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_breaker_opens_after_n_failures_and_probes_once_after_the_reset_time():
    clock = Clock()
    breaker = CircuitBreaker(failures=3, reset_seconds=30, clock=clock)
    assert breaker.state == "closed"
    for _ in range(3):
        assert breaker.allow()
        breaker.record_failure()
    assert breaker.state == "open" and not breaker.allow()

    clock.now = 31
    assert breaker.state == "half-open"
    assert breaker.allow()  # the single trial call
    assert not breaker.allow()  # everyone else still fails fast while the trial is running
    breaker.record_success()
    assert breaker.state == "closed" and breaker.allow()


def test_a_failed_trial_reopens_the_breaker_for_another_full_period():
    clock = Clock()
    breaker = CircuitBreaker(failures=1, reset_seconds=30, clock=clock)
    breaker.record_failure()
    clock.now = 31
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.now = 50  # only 19 s after the failed trial
    assert not breaker.allow()


def http(handler, **kw):
    return ResilientHttp(
        "orders",
        "http://orders",
        {},
        transport=httpx.MockTransport(handler),
        backoff_initial=0.001,
        **kw,
    )


def test_a_transient_503_is_retried_until_it_works():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={"ok": True})

    assert http(handler, retries=3).request("GET", "/x") == {"ok": True}
    assert len(calls) == 3


def test_a_connection_error_is_retried_then_reported_as_unavailable():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(ServiceUnavailable):
        http(handler, retries=3).request("GET", "/x")
    assert len(calls) == 3


def test_404_is_an_answer_not_a_failure_and_is_never_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    client = http(handler, retries=3)
    for _ in range(10):  # ten "not found" in a row must NOT open the breaker
        with pytest.raises(NotFound):
            client.request("GET", "/x")
    assert len(calls) == 10 and client.breaker.state == "closed"


def test_a_rejected_request_is_not_retried_and_does_not_trip_the_breaker():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    client = http(handler, retries=3)
    with pytest.raises(ServiceUnavailable, match="rejected"):
        client.request("GET", "/x")
    assert len(calls) == 1 and client.breaker.state == "closed"


def test_when_the_breaker_is_open_the_service_is_not_called_at_all():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    client = http(handler, retries=1, breaker=CircuitBreaker(failures=2, reset_seconds=60))
    for _ in range(2):
        with pytest.raises(ServiceUnavailable):
            client.request("GET", "/x")
    assert len(calls) == 2
    with pytest.raises(ServiceUnavailable, match="temporarily"):
        client.request("GET", "/x")
    assert len(calls) == 2  # failed fast: no third call
