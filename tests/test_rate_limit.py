"""Per-client sliding-window limiter (SPEC §2.3, §8)."""

from recipe_qa.api import RateLimiter


def test_limit_applies_per_client_within_a_minute() -> None:
    limiter = RateLimiter(per_minute=2)
    assert limiter.allow("a", now=0)
    assert limiter.allow("a", now=1)
    assert not limiter.allow("a", now=2)
    assert limiter.allow("b", now=2)
    assert limiter.allow("a", now=60.5)


def test_inactive_clients_are_forgotten() -> None:
    # A public instance sees many one-off addresses; memory must not grow
    # with every client ever seen.
    limiter = RateLimiter(per_minute=30)
    for i in range(1000):
        limiter.allow(f"10.0.{i // 256}.{i % 256}", now=i * 0.01)
    assert limiter.tracked_clients() == 1000

    limiter.allow("late", now=200)
    assert limiter.tracked_clients() == 1
