"""
Token-bucket behaviour, queue priority and cooldown.

A fake clock keeps the arithmetic deterministic; no real waiting.
"""

from lute.ai.limiter import (
    INTERACTIVE,
    PREFETCH,
    RateLimiter,
    normalize_rpm,
)


class FakeClock:
    "Monotonic clock the test advances by hand."

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _take(limiter, provider="p", rpm=20, priority=INTERACTIVE):
    "Reserve one ticket and return its poll result."
    return limiter.poll(limiter.reserve(provider, priority), rpm)


def test_rpm_is_clamped_to_the_allowed_range():
    assert normalize_rpm(20) == 20
    assert normalize_rpm(0) == 1
    assert normalize_rpm(900) == 600
    assert normalize_rpm("junk") == 20
    assert normalize_rpm(None) == 20


def test_bucket_starts_with_a_burst_of_four_then_refills_every_three_seconds():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(4):
        assert _take(limiter)["state"] == "granted"
    queued = limiter.reserve("p", INTERACTIVE)
    status = limiter.poll(queued, 20)
    assert status["state"] == "queued"
    assert status["ahead"] == 0
    assert status["eta"] == 3
    clock.advance(3)
    assert limiter.poll(queued, 20)["state"] == "granted"


def test_low_rpm_shrinks_the_burst():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    assert _take(limiter, rpm=1)["state"] == "granted"
    second = limiter.reserve("p", INTERACTIVE)
    status = limiter.poll(second, 1)
    assert status["state"] == "queued"
    clock.advance(60)
    assert limiter.poll(second, 1)["state"] == "granted"


def test_interactive_requests_are_served_before_prefetch():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(4):
        assert _take(limiter)["state"] == "granted"
    prefetch = limiter.reserve("p", PREFETCH)
    live = limiter.reserve("p", INTERACTIVE)
    clock.advance(3)
    assert limiter.poll(live, 20)["state"] == "granted"
    assert limiter.poll(prefetch, 20)["state"] == "queued"
    clock.advance(3)
    assert limiter.poll(prefetch, 20)["state"] == "granted"


def test_cooldown_is_reported_and_expires():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limiter.cool("p", 60)
    ticket = limiter.reserve("p", INTERACTIVE)
    cooling = limiter.poll(ticket, 20)
    assert cooling["state"] == "cooling"
    assert cooling["cooling"] == 60
    clock.advance(61)
    assert limiter.poll(ticket, 20)["state"] == "granted"


def test_cancel_reports_cancelled():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    ticket = limiter.reserve("p", INTERACTIVE)
    limiter.cancel(ticket)
    assert limiter.poll(ticket, 20)["state"] == "cancelled"


def test_snapshot_observes_queue_length_and_cooldown():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(4):
        assert _take(limiter)["state"] == "granted"
    limiter.reserve("p", PREFETCH)
    limiter.reserve("p", INTERACTIVE)
    snapshot = limiter.snapshot("p", 20)
    assert snapshot["queued"] == 2
    assert snapshot["cooling"] == 0
    assert snapshot["rpm"] == 20
    limiter.cool("p", 30)
    assert limiter.snapshot("p", 20)["cooling"] > 0
