"""What the server says about your budget, and how to stay inside it.

TWO BUDGETS, SEPARATELY METERED. The API meters requests per minute, and
history requests again per hour. They are different windows over different
counters, so the server advertises them in two sets of headers:

    RateLimit-Limit / -Policy / -Remaining / -Reset            per minute
    RateLimit-History-Limit / -Policy / -Remaining / -Reset    per hour

Both were being thrown away. The SDK only ever read `Retry-After`, and only
after a 429 had already happened -- so it could tell you that you had run out,
never that you were about to.

ABSENCE IS NOT ZERO. A response a shared cache may store carries no per-caller
figures at all, because they belong to whoever populated the cache entry. That
is every anonymous response. So `None` here means "the server did not say",
which is a different thing from "nothing left", and nothing in this module may
confuse the two.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

#: Header prefixes for the two meters.
_REST_PREFIX = "ratelimit"
_HISTORY_PREFIX = "ratelimit-history"


def _int_or_none(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return None


@dataclass(frozen=True)
class RateLimit:
    """One meter's state, as of the last response that mentioned it.

    Every field is optional because every field can be legitimately absent:
    an unmetered plan advertises nothing, and neither does a publicly
    cacheable response.
    """

    #: Requests allowed per window, or None if the server did not say.
    limit: int | None = None
    #: Requests left in the current window, or None if the server did not say.
    remaining: int | None = None
    #: Seconds until the window resets, as of `observed_at`.
    reset: int | None = None
    #: The raw policy string, e.g. "300;w=60".
    policy: str | None = None
    #: `time.monotonic()` when this was read, so `reset` can be aged.
    observed_at: float | None = None

    @property
    def exhausted(self) -> bool:
        """True only when the server SAID there is nothing left.

        An unknown remaining is not exhaustion. Treating it as such would make
        an anonymous caller, whose responses never carry figures, wait forever.
        """
        return self.remaining == 0

    def seconds_until_reset(self, now: float | None = None) -> float | None:
        """How long is left of the window, counting down from when we read it.

        `reset` is a relative value frozen at `observed_at`; using it later
        without ageing it is how a client waits far longer than it needs to.
        """
        if self.reset is None or self.observed_at is None:
            return None
        elapsed = (time.monotonic() if now is None else now) - self.observed_at
        return max(0.0, self.reset - elapsed)


@dataclass(frozen=True)
class RateLimits:
    """Both meters. Reached as `client.rate_limit`."""

    rest: RateLimit = field(default_factory=RateLimit)
    history: RateLimit = field(default_factory=RateLimit)


def _read_one(headers: Mapping[str, str], prefix: str, now: float) -> RateLimit:
    limit = _int_or_none(headers.get(f"{prefix}-limit"))
    remaining = _int_or_none(headers.get(f"{prefix}-remaining"))
    reset = _int_or_none(headers.get(f"{prefix}-reset"))
    policy = headers.get(f"{prefix}-policy")
    if limit is None and remaining is None and reset is None and policy is None:
        return RateLimit()
    return RateLimit(limit=limit, remaining=remaining, reset=reset, policy=policy, observed_at=now)


def read_rate_limits(headers: Mapping[str, str], previous: RateLimits) -> RateLimits:
    """Merge whatever this response said into what we already knew.

    A response that mentions neither meter leaves both alone. That matters
    because most responses mention only one: the history headers appear on
    history routes, and on a cacheable response neither appears. Overwriting
    with blanks would mean the last cacheable response erased everything the
    SDK had learned.

    Header lookup is case-insensitive via httpx's own mapping, but a plain dict
    is accepted too, so the keys are compared lowercased.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    # No prefix filtering needed: _read_one looks up EXACT keys, so
    # "ratelimit-limit" and "ratelimit-history-limit" cannot collide. An
    # earlier version filtered the history keys out before reading the REST
    # meter; removing that filter changed no behaviour and no test, which is
    # what a redundant guard looks like. The bleed it guarded against is
    # covered by a test either way.
    now = time.monotonic()
    history = _read_one(lowered, _HISTORY_PREFIX, now)
    rest = _read_one(lowered, _REST_PREFIX, now)
    return RateLimits(
        rest=rest if rest.observed_at is not None else previous.rest,
        history=history if history.observed_at is not None else previous.history,
    )


class Gate:
    """One shared "not before" instant for a whole client.

    WHY SHARED. A 429 applies to the CALLER, not to the request that happened
    to meet it. With a per-request backoff, ten concurrent requests each sleep
    their own Retry-After and then all retry at the same instant, re-tripping
    the limit together -- a thundering herd the client inflicts on itself, and
    on us. One gate means the wait is taken once.

    Each waiter adds its own small jitter on the way out, because waking
    together is the other half of the same problem.
    """

    def __init__(self, jitter: float = 0.25) -> None:
        self._until = 0.0
        self._jitter = jitter
        self._lock = threading.Lock()

    def close_for(self, seconds: float) -> None:
        """Hold every request on this client for at least `seconds`."""
        deadline = time.monotonic() + max(0.0, seconds)
        with self._lock:
            # Never bring the gate forward: a shorter Retry-After arriving
            # while a longer one is in force would release the herd early.
            self._until = max(self._until, deadline)

    def wait_seconds(self) -> float:
        """How long this caller should hold off, jitter included. 0 if open."""
        with self._lock:
            remaining = self._until - time.monotonic()
        if remaining <= 0:
            return 0.0
        return remaining + random.random() * self._jitter
