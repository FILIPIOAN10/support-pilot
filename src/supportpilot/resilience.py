"""Calling other services without letting their bad day become ours: timeout + retry with backoff + circuit breaker.

timeout   never wait forever: a hung dependency would pin one of our threads per request until we run out
retry     a blip (503, connection reset) usually passes in 200 ms - but only for calls that are safe to repeat
breaker   when a service keeps failing, stop calling it for a while: fail fast (and fast is kind to both sides)
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)


class ServiceUnavailable(Exception):
    """The dependency could not be used (down, too slow, or the breaker is open). Message is safe to show."""


class NotFound(Exception):
    """The dependency answered 404: a business answer, not a failure (never retried, never trips the breaker)."""


class Conflict(Exception):
    """The dependency answered 409: it understood and said no ("this order cannot be returned"). A business answer
    like 404 - never retried, never trips the breaker. The message is the dependency's reason, safe to show."""


class Transient(Exception):
    """A response that is worth retrying: 5xx and 429."""


class CircuitBreaker:
    """closed -> (N failures in a row) -> open -> (reset_seconds pass) -> half-open -> one trial call -> closed / open."""

    def __init__(
        self,
        failures: int = 5,
        reset_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_failures = failures
        self._reset = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if self._clock() - self._opened_at >= self._reset else "open"

    def allow(self) -> bool:
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            if state == "half-open" and not self._trial_in_flight:
                self._trial_in_flight = True  # exactly one caller probes; the rest keep failing fast
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures, self._opened_at, self._trial_in_flight = 0, None, False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._trial_in_flight = False
            if self._failures >= self._max_failures or self._opened_at is not None:
                self._opened_at = self._clock()


class ResilientHttp:
    """An httpx client with explicit timeouts, retries and a breaker. One instance per dependency."""

    def __init__(
        self,
        name: str,
        base_url: str,
        headers: dict[str, str],
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 8.0,
        retries: int = 3,
        breaker: CircuitBreaker | None = None,
        transport: httpx.BaseTransport | None = None,
        backoff_initial: float = 0.2,
    ):
        self.name = name
        self._retries = retries
        self._backoff_initial = backoff_initial
        self.breaker = breaker or CircuitBreaker()
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Returns the decoded JSON. Raises NotFound (404) or ServiceUnavailable (everything else that goes wrong)."""
        if not self.breaker.allow():
            raise ServiceUnavailable(f"{self.name} is temporarily unavailable")
        try:
            result = self._with_retries(method, path, **kwargs)
        except (NotFound, Conflict):
            self.breaker.record_success()  # the service is fine, it just said "no such thing" / "no"
            raise
        except (Transient, httpx.TransportError) as e:
            self.breaker.record_failure()
            log.warning("%s failed after retries: %s", self.name, type(e).__name__)
            raise ServiceUnavailable(f"{self.name} is unavailable") from e
        except httpx.HTTPStatusError as e:  # 400/401/403...: OUR request is wrong (bad key?), retrying cannot help
            self.breaker.record_success()
            log.error("%s rejected our request: %s", self.name, e.response.status_code)
            raise ServiceUnavailable(f"{self.name} rejected the request") from e
        self.breaker.record_success()
        return result

    def _with_retries(self, method: str, path: str, **kwargs: Any) -> Any:
        retrying = Retrying(
            stop=stop_after_attempt(self._retries),
            # waits ~0.2 s, 0.4 s, 0.8 s ... (capped at 2 s) plus a random extra, so many callers do not retry in lockstep
            wait=wait_exponential_jitter(multiplier=self._backoff_initial, jitter=self._backoff_initial, max=2.0),
            retry=retry_if_exception_type((Transient, httpx.TransportError)),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                response = self._client.request(method, path, **kwargs)
                if response.status_code == 404:
                    raise NotFound(path)
                if response.status_code == 409:
                    raise Conflict(_detail(response))
                if response.status_code >= 500 or response.status_code == 429:
                    raise Transient(f"{response.status_code}")
                response.raise_for_status()  # other 4xx: our bug (bad key, bad request) - not retryable
                return response.json()

    def close(self) -> None:
        self._client.close()


def _detail(response: httpx.Response) -> str:
    """FastAPI and the shop's internal API both answer {"detail": "..."}; anything else gets a generic reason."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail)[:300] if detail else "the request was refused"
