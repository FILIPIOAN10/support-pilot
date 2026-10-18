"""The real clients (httpx + retries + breaker) against the real orders mock over a real socket."""

import time

import pytest

from supportpilot import orders_mock
from supportpilot.clients import make_orders_client
from supportpilot.config import Settings
from supportpilot.resilience import ServiceUnavailable
from tests.servers import RunningApp


@pytest.fixture
def orders_client():
    orders_mock.mode["value"] = "ok"
    orders_mock.REFUNDS.clear()
    with RunningApp(orders_mock.app) as server:
        settings = Settings(
            _env_file=None,
            orders_base_url=server.url,
            http_retries=3,
            breaker_failures=2,
            breaker_reset_seconds=60,
            http_read_timeout=0.5,
        )
        yield make_orders_client(settings)
    orders_mock.mode["value"] = "ok"


def test_reads_an_order_and_maps_404_to_none(orders_client):
    assert orders_client.get_order("1001")["customer_id"] == "c-100"
    assert orders_client.get_order("9999") is None


def test_a_refund_sent_twice_with_the_same_key_is_one_refund(orders_client):
    a = orders_client.create_refund("1001", "too small", "conv:1001")
    b = orders_client.create_refund("1001", "too small", "conv:1001")
    assert a == b


def test_when_the_service_fails_the_client_gives_up_and_then_fails_fast(orders_client):
    orders_mock.mode["value"] = "fail"
    for _ in range(2):  # two failed calls (each with its retries) open the breaker
        with pytest.raises(ServiceUnavailable, match="unavailable"):
            orders_client.get_order("1001")
    orders_mock.mode["value"] = "ok"  # the service is healthy again, but the breaker is still open
    started = time.perf_counter()
    with pytest.raises(ServiceUnavailable, match="temporarily"):
        orders_client.get_order("1001")
    assert time.perf_counter() - started < 0.05


def test_a_slow_service_hits_the_read_timeout(orders_client):
    orders_mock.mode["value"] = "slow"
    started = time.perf_counter()
    with pytest.raises(ServiceUnavailable):
        orders_client.get_order("1001")
    assert time.perf_counter() - started < 8  # 3 attempts x 0.5 s timeout + backoff, not the 10 s the service sleeps
