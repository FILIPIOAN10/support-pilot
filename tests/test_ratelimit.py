import redis

from supportpilot.ratelimit import RateLimiter


def test_requests_up_to_the_limit_pass_and_the_next_one_is_blocked(redis_client):
    limiter = RateLimiter(redis_client, limit=3)
    assert [limiter.hit("c-1")[0] for _ in range(5)] == [True, True, True, False, False]


def test_customers_are_counted_separately(redis_client):
    limiter = RateLimiter(redis_client, limit=1)
    assert limiter.hit("c-1")[0] and limiter.hit("c-2")[0] and not limiter.hit("c-1")[0]


def test_the_counter_always_has_an_expiry_so_nobody_is_blocked_forever(redis_client):
    limiter = RateLimiter(redis_client, limit=1, window_seconds=60)
    limiter.hit("c-1")
    limiter.hit("c-1")
    assert 0 < redis_client.ttl("rl:c-1") <= 60


def test_retry_after_is_the_time_left_in_the_window(redis_client):
    limiter = RateLimiter(redis_client, limit=1, window_seconds=60)
    limiter.hit("c-1")
    allowed, retry_after = limiter.hit("c-1")
    assert not allowed and 1 <= retry_after <= 60


def test_when_redis_is_down_the_limiter_lets_requests_through():
    # built like the app builds its client (from_url): redis.Redis(...) would retry by default and take seconds
    dead = redis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
    assert RateLimiter(dead, limit=1).hit("c-1") == (True, 0)
