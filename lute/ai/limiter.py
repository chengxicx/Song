"""
Per-provider token buckets for every outbound AI request.

Each provider entry gets its own bucket: capacity 4, one token every
``60 / rpm`` seconds (20 rpm by default).  A request reserves a ticket
and then polls; the route can emit a ``queued`` / ``cooling`` state while
it waits, so the panel shows "3 ahead" instead of a spinner.  Live
requests (sentence, grammar, Test connection) are served before prefetch
tickets, and each class stays FIFO.

Nothing here touches HTTP or the database; it is deliberately small so
the bucket arithmetic and the 429 cooldown can be unit-tested with a fake
clock.
"""

import math
import threading
import time
from collections import deque

INTERACTIVE = "interactive"
PREFETCH = "prefetch"

DEFAULT_RPM = 20
MIN_RPM = 1
MAX_RPM = 600
CAPACITY = 4
COOLDOWN_SECONDS = 60


def normalize_rpm(value, default=DEFAULT_RPM):
    "An rpm the bucket can use: 1..600, defaulting when unset or junk."
    try:
        rpm = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_RPM, min(MAX_RPM, rpm))


def _capacity(rpm):
    "Never allow a larger burst than an rpm can sustain in a minute."
    return max(1, min(CAPACITY, rpm))


class Ticket:  # pylint: disable=too-few-public-methods
    "One waiting request; the route keeps it to poll and cancel."

    __slots__ = ("provider_id", "priority", "created", "cancelled")

    def __init__(self, provider_id, priority):
        self.provider_id = provider_id
        self.priority = priority
        self.created = time.monotonic()
        self.cancelled = False


class _Bucket:  # pylint: disable=too-few-public-methods
    "Tokens plus the two FIFO queues for one provider entry."

    def __init__(self, clock):
        self.tokens = _capacity(DEFAULT_RPM)
        self.rpm = DEFAULT_RPM
        self.last_refill = clock()
        self.cooling_until = 0.0
        self.queues = {INTERACTIVE: deque(), PREFETCH: deque()}


class RateLimiter:
    "Process-wide rate limiter, one bucket per provider entry id."

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._buckets = {}

    def reset(self):
        "Drop every bucket; tests only."
        with self._lock:
            self._buckets = {}

    # -- internals -----------------------------------------------------

    def _bucket(self, provider_id):
        key = provider_id or ""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(self._clock)
            self._buckets[key] = bucket
        return bucket

    def _refill(self, bucket, rpm):
        now = self._clock()
        rpm = normalize_rpm(rpm)
        interval = 60.0 / rpm
        elapsed = max(0.0, now - bucket.last_refill)
        bucket.tokens = bucket.tokens + elapsed / interval
        # A Settings change (or a low rpm entry) must shrink the burst
        # immediately, not only after the next refill.
        if bucket.rpm != rpm:
            bucket.rpm = rpm
            bucket.tokens = min(bucket.tokens, _capacity(rpm))
        bucket.tokens = min(_capacity(rpm), bucket.tokens)
        bucket.last_refill = now

    @staticmethod
    def _head(bucket):
        "The ticket that must be served next, interactive first, FIFO."
        for queue in (bucket.queues[INTERACTIVE], bucket.queues[PREFETCH]):
            for ticket in queue:
                if not ticket.cancelled:
                    return ticket
        return None

    @staticmethod
    def _ahead(bucket, ticket):
        "How many live tickets are before this one."
        ahead = 0
        for priority in (INTERACTIVE, PREFETCH):
            for item in bucket.queues[priority]:
                if item.cancelled:
                    continue
                if item is ticket:
                    return ahead
                ahead += 1
        return ahead

    def _remove(self, bucket, ticket):
        queue = bucket.queues[ticket.priority]
        try:
            queue.remove(ticket)
        except ValueError:
            pass

    # -- API -----------------------------------------------------------

    def reserve(self, provider_id, priority=INTERACTIVE):
        "Queue a request; poll() grants it once a token is available."
        ticket = Ticket(provider_id, priority)
        with self._lock:
            self._bucket(provider_id).queues[priority].append(ticket)
        return ticket

    def poll(self, ticket, rpm=DEFAULT_RPM):
        """
        The ticket's status: granted, cooling, queued or cancelled.

        ``granted`` consumes the token.  Callers must stop polling after it.
        ``queued`` carries the number ahead and an ETA in whole seconds.
        """
        with self._lock:
            bucket = self._bucket(ticket.provider_id)
            self._refill(bucket, rpm)
            now = self._clock()
            if ticket.cancelled:
                return {"state": "cancelled", "ahead": 0, "eta": 0, "cooling": 0}
            cooling = max(0, int(math.ceil(bucket.cooling_until - now)))
            if cooling > 0:
                return {
                    "state": "cooling",
                    "ahead": self._ahead(bucket, ticket),
                    "eta": cooling,
                    "cooling": cooling,
                }
            if self._head(bucket) is ticket and bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                self._remove(bucket, ticket)
                return {"state": "granted", "ahead": 0, "eta": 0, "cooling": 0}
            ahead = self._ahead(bucket, ticket)
            interval = 60.0 / normalize_rpm(rpm)
            eta = int(math.ceil((ahead + 1) * interval))
            return {
                "state": "queued",
                "ahead": ahead,
                "eta": eta,
                "cooling": 0,
            }

    def cancel(self, ticket):
        "Forget a waiting ticket (client disconnected)."
        with self._lock:
            ticket.cancelled = True
            self._remove(self._bucket(ticket.provider_id), ticket)

    def cool(self, provider_id, seconds=COOLDOWN_SECONDS):
        "Block the entry for a while after consecutive 429 responses."
        with self._lock:
            bucket = self._bucket(provider_id)
            bucket.cooling_until = max(
                bucket.cooling_until, self._clock() + max(0, seconds)
            )
            return seconds

    def cooling_seconds(self, provider_id):
        "Seconds left in the entry's cooldown, 0 when ready."
        with self._lock:
            bucket = self._bucket(provider_id)
            return max(0, int(math.ceil(bucket.cooling_until - self._clock())))

    def snapshot(self, provider_id, rpm=DEFAULT_RPM):
        "Observable queue state for /ai/queue and the SSE states."
        with self._lock:
            bucket = self._bucket(provider_id)
            self._refill(bucket, rpm)
            now = self._clock()
            cooling = max(0, int(math.ceil(bucket.cooling_until - now)))
            queued = sum(
                1
                for queue in bucket.queues.values()
                for item in queue
                if not item.cancelled
            )
            return {
                "queued": queued,
                "cooling": cooling,
                "tokens": round(bucket.tokens, 2),
                "rpm": normalize_rpm(rpm),
            }

    def acquire(self, provider_id, rpm=DEFAULT_RPM, priority=PREFETCH, cancel=None):
        """
        Blocking acquire for the prefetch worker (no SSE to report to).

        Polls in 0.2s slices so a cancelled context can drop out promptly.
        """
        ticket = self.reserve(provider_id, priority)
        try:
            while True:
                if cancel is not None and cancel():
                    self.cancel(ticket)
                    return False
                status = self.poll(ticket, rpm)
                if status["state"] == "granted":
                    return True
                if status["state"] == "cancelled":
                    return False
                time.sleep(0.2)
        except Exception:  # pragma: no cover - defensive
            self.cancel(ticket)
            raise


LIMITER = RateLimiter()
