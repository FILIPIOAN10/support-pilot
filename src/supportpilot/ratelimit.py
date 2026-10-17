"""Per-customer rate limit in Redis: a fixed window counter.

Every model call costs money, so a loop (or a script) hammering /v1/chat is a bill, not just load.
INCR + EXPIRE run inside one Lua script, so Redis executes them as a single step: no other client can slip in between
"increment" and "set the expiry", and a counter can never be left without an expiry (which would block a customer forever).
"""

import logging

import redis

log = logging.getLogger(__name__)

SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""


class RateLimiter:
    def __init__(self, client: redis.Redis, limit: int, window_seconds: int = 60):
        self._limit = limit
        self._window = window_seconds
        self._script = client.register_script(SCRIPT)

    def hit(self, key: str) -> tuple[bool, int]:
        """(allowed, seconds until the window resets). Fails OPEN: if Redis is down the service keeps working,
        because a broken limiter must not become an outage (the model-call limits still cap each conversation)."""
        try:
            count, ttl = self._script(keys=[f"rl:{key}"], args=[self._window])
        except redis.RedisError:
            log.warning("rate limiter unavailable, allowing the request")
            return True, 0
        return count <= self._limit, max(int(ttl), 1)
