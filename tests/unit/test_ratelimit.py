"""Reading the two budgets, and staying inside them.

The SDK used to read exactly one header, `Retry-After`, and only after a 429
had already happened. It could tell you that you had run out, never that you
were about to. These cover the three things that changed: the figures are
read, absence is not confused with zero, and the wait a 429 imposes is taken
ONCE for the whole client rather than once per in-flight request.
"""

import time

import httpx
import pytest

import themeparks._ratelimit as rl
from themeparks import RateLimitError, RateLimits, RetryConfig, ThemeParks
from themeparks._ratelimit import Gate, RateLimit, read_rate_limits

REST = {
    "RateLimit-Limit": "300",
    "RateLimit-Policy": "300;w=60",
    "RateLimit-Remaining": "299",
    "RateLimit-Reset": "60",
}
HISTORY = {
    "RateLimit-History-Limit": "600",
    "RateLimit-History-Policy": "600;w=3600",
    "RateLimit-History-Remaining": "599",
    "RateLimit-History-Reset": "3412",
}


class TestReadingHeaders:
    def test_reads_the_rest_meter(self):
        out = read_rate_limits(REST, RateLimits())
        assert (out.rest.limit, out.rest.remaining, out.rest.reset) == (300, 299, 60)
        assert out.rest.policy == "300;w=60"

    def test_reads_the_history_meter(self):
        out = read_rate_limits(HISTORY, RateLimits())
        assert (out.history.limit, out.history.remaining, out.history.reset) == (600, 599, 3412)

    def test_the_two_meters_do_not_bleed_into_each_other(self):
        # "ratelimit-history-limit" also starts with "ratelimit-", so a naive
        # prefix match reads the hourly figure as the per-minute one and a
        # client paces itself against the wrong window.
        out = read_rate_limits({**REST, **HISTORY}, RateLimits())
        assert out.rest.limit == 300
        assert out.history.limit == 600
        assert out.rest.reset == 60
        assert out.history.reset == 3412

    def test_history_headers_alone_do_not_invent_a_rest_meter(self):
        out = read_rate_limits(HISTORY, RateLimits())
        assert out.rest.limit is None

    def test_a_response_mentioning_neither_keeps_what_we_knew(self):
        # Most responses mention one meter or, if publicly cacheable, neither.
        # Overwriting with blanks would mean the last cacheable response
        # erased everything the client had learned.
        known = read_rate_limits({**REST, **HISTORY}, RateLimits())
        after = read_rate_limits({"content-type": "application/json"}, known)
        assert after.rest.remaining == 299
        assert after.history.remaining == 599

    def test_header_case_does_not_matter(self):
        out = read_rate_limits({k.lower(): v for k, v in REST.items()}, RateLimits())
        assert out.rest.remaining == 299

    def test_a_malformed_value_is_unknown_rather_than_zero(self):
        # An int() that fails must not become 0, or the client would hold
        # forever waiting for a window it invented.
        out = read_rate_limits({**REST, "RateLimit-Remaining": "lots"}, RateLimits())
        assert out.rest.remaining is None
        assert out.rest.exhausted is False


class TestACachedResponseSaysNothing:
    """Its figures belong to whoever populated the entry.

    The server withholds the HISTORY figures from anything a shared cache may
    store, but the per-minute ones ride those responses. Measured against
    production: three consecutive calls returning `age: 9` and an unmoving
    `remaining: 285`. A cached `remaining: 0` would make the client sleep out
    a window belonging to someone else.
    """

    def test_a_cache_hit_is_ignored(self):
        known = read_rate_limits(REST, RateLimits())
        after = read_rate_limits({**REST, "RateLimit-Remaining": "0", "Age": "1713"}, known)
        assert after.rest.remaining == 299, "took a cached caller's figures"

    def test_a_fresh_response_is_recorded(self):
        # A cache MISS carries no Age at all, which is the path that matters:
        # confirmed against production, a MISS returns the figures and a HIT
        # returns them stale.
        out = read_rate_limits(REST, RateLimits())
        assert out.rest.remaining == 299

    def test_age_zero_is_fresh(self):
        out = read_rate_limits({**REST, "Age": "0"}, RateLimits())
        assert out.rest.remaining == 299

    def test_a_cache_hit_does_not_erase_what_we_knew(self):
        known = read_rate_limits({**REST, **HISTORY}, RateLimits())
        after = read_rate_limits({"Age": "60"}, known)
        assert after.rest.remaining == 299
        assert after.history.remaining == 599


class TestAbsenceIsNotZero:
    def test_unknown_remaining_is_not_exhausted(self):
        # Anonymous responses carry no figures at all, because they are
        # publicly cacheable and the figures are per-caller. Reading that as
        # "nothing left" would stall every anonymous client permanently.
        assert RateLimit().exhausted is False

    def test_zero_remaining_is_exhausted(self):
        assert RateLimit(remaining=0).exhausted is True

    # Both of these pass an explicit `now` rather than reading a clock. The
    # method takes one precisely so these can be exact; asserting a range
    # around real wall-clock time makes a gate test flaky under CPU load.
    def test_reset_counts_down_from_when_it_was_read(self):
        # `reset` is relative and frozen at observed_at. Using it later
        # without ageing it is how a client waits far longer than it needs to.
        meter = RateLimit(reset=60, observed_at=1000.0)
        assert meter.seconds_until_reset(now=1050.0) == 10.0

    def test_an_expired_window_never_reports_negative(self):
        meter = RateLimit(reset=5, observed_at=1000.0)
        assert meter.seconds_until_reset(now=1100.0) == 0.0

    def test_unknown_reset_has_no_countdown(self):
        assert RateLimit(remaining=0).seconds_until_reset() is None


class TestGate:
    """One wait for the whole client, not one per in-flight request."""

    def test_an_open_gate_costs_nothing(self):
        assert Gate().wait_seconds() == 0.0

    def test_a_closed_gate_holds_every_caller(self):
        gate = Gate()
        gate.close_for(5)
        first, second = gate.wait_seconds(), gate.wait_seconds()
        assert first > 5.0 and second > 5.0

    def test_waiters_are_jittered_so_they_do_not_wake_together(self):
        # Waking in unison is the other half of the thundering herd: the
        # sleeps are shared, then everyone retries at the same instant and
        # re-trips the limit.
        gate = Gate()
        gate.close_for(5)
        waits = {gate.wait_seconds() for _ in range(20)}
        assert len(waits) > 1

    def test_a_shorter_wait_never_brings_the_gate_forward(self):
        # A 2-second Retry-After arriving while a 60-second one is in force
        # would otherwise release the herd early.
        gate = Gate()
        gate.close_for(60)
        gate.close_for(2)
        assert gate.wait_seconds() > 55.0


class TestThroughTheClient:
    def _client(self, headers, slept=None):
        def handler(request):
            return httpx.Response(200, headers=headers, json={"destinations": []})

        tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False)
        if slept is not None:
            tp.raw._t._sleep = slept.append
        return tp

    def test_a_real_call_records_both_meters(self):
        tp = self._client({**REST, **HISTORY})
        tp.destinations.list()
        assert tp.rate_limit.rest.remaining == 299
        assert tp.rate_limit.history.remaining == 599

    def test_before_any_call_everything_is_unknown(self):
        tp = self._client(REST)
        assert tp.rate_limit.rest.limit is None
        assert tp.rate_limit.rest.exhausted is False

    def test_a_spent_window_is_waited_out_rather_than_walked_into(self):
        # Sending into a window the server said is spent is a guaranteed 429
        # that also costs a unit of budget to refuse.
        slept: list[float] = []
        tp = self._client({**REST, "RateLimit-Remaining": "0", "RateLimit-Reset": "7"}, slept)
        tp.destinations.list()  # learns remaining 0
        tp.destinations.list()  # should hold first
        assert slept, "walked straight into a window the server said was spent"
        # Upper bound allows the spread now applied to this path: without it
        # every waiter woke at the same absolute instant.
        assert 0 < slept[0] <= 7.0 + 0.25

    def test_an_unknown_remaining_never_holds(self):
        slept: list[float] = []
        tp = self._client({"content-type": "application/json"}, slept)
        tp.destinations.list()
        tp.destinations.list()
        assert slept == []

    def test_respect_remaining_can_be_turned_off(self):
        slept: list[float] = []
        headers = {**REST, "RateLimit-Remaining": "0", "RateLimit-Reset": "7"}

        def handler(request):
            return httpx.Response(200, headers=headers, json={"destinations": []})

        tp = ThemeParks(
            transport=httpx.MockTransport(handler),
            cache=False,
            retry=RetryConfig(respect_remaining=False),
        )
        tp.raw._t._sleep = slept.append
        tp.destinations.list()
        tp.destinations.list()
        assert slept == []


class TestThroughTheCache:
    """Caching is ON by default, and it wraps the transport.

    Every other test here passes `cache=False`, so none of them touched the
    wrapper. The first real call against production raised AttributeError:
    the caching transport had no `rate_limit` to forward. Tests that all take
    the same non-default path cover a shape the users do not have.
    """

    def _client(self, headers):
        def handler(request):
            return httpx.Response(200, headers=headers, json={"destinations": []})

        return ThemeParks(transport=httpx.MockTransport(handler))  # cache default: ON

    def test_the_figures_survive_the_cache_wrapper(self):
        tp = self._client(REST)
        tp.destinations.list()
        assert tp.rate_limit.rest.remaining == 299

    def test_a_cache_hit_keeps_the_last_known_figures(self):
        # A hit sends no request and so learns nothing, which is right: it
        # spent no budget either, so the previous figures still stand.
        calls: list[int] = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, headers=REST, json={"destinations": []})

        tp = ThemeParks(transport=httpx.MockTransport(handler))
        tp.destinations.list()
        tp.destinations.list()
        assert len(calls) == 1, "expected the second call to be a cache hit"
        assert tp.rate_limit.rest.remaining == 299


class TestTheOptOutsActuallyOptOut:
    """An advertised switch that does not switch anything is worse than none.

    `respect_429=False` raised the RateLimitError the caller asked for, and
    then closed the shared gate anyway, so their NEXT call blocked for the
    full Retry-After with no way to stop it. The setting says "do not wait on
    a 429"; the gate is a wait on a 429.
    """

    def _client(self, retry, slept):
        def handler(request):
            return httpx.Response(429, headers={"retry-after": "45"}, json={})

        tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False, retry=retry)
        tp.raw._t._sleep = slept.append
        return tp

    def test_respect_429_false_never_sleeps_even_on_a_later_call(self):
        slept: list[float] = []
        tp = self._client(RetryConfig(respect_429=False), slept)
        for _ in range(3):
            with pytest.raises(RateLimitError):
                tp.destinations.list()
        assert slept == [], "opted out of 429 waiting and waited anyway"

    def test_respect_429_true_still_holds_the_gate(self):
        # The opt-out must not have disabled the feature for everyone else.
        slept: list[float] = []
        tp = self._client(RetryConfig(max_retries=0), slept)
        with pytest.raises(RateLimitError):
            tp.destinations.list()
        with pytest.raises(RateLimitError):
            tp.destinations.list()
        assert slept, "the gate stopped holding for callers who did want it"


class TestTheCapBoundsTheWholeCall:
    """`max_retry_after` promises a ceiling. It has to be a real one.

    Two separate self-initiated holds exist -- the shared gate, and the
    spent-window wait -- and they stack. A 429 carrying BOTH a `Retry-After`
    and `RateLimit-Remaining: 0` slept 5s at the gate and then 55s for the
    window, three times over: 180 seconds inside one call whose cap was 120.
    Each leg was under the cap, so the per-leg check never fired.

    Nothing caught it because no test sent a 429 carrying rate-limit headers,
    and because a fake sleep that does not advance the clock cannot show a
    cumulative total at all. This one advances an injected clock, which is
    what the real world does.
    """

    def _run(self, cap, headers):
        now = [1000.0]
        original = rl.time.monotonic
        rl.time.monotonic = lambda: now[0]
        try:
            slept: list[float] = []

            def sleep(seconds):
                slept.append(seconds)
                now[0] += seconds

            def handler(request):
                return httpx.Response(429, headers=headers, json={})

            tp = ThemeParks(
                transport=httpx.MockTransport(handler),
                cache=False,
                retry=RetryConfig(max_retry_after=cap),
            )
            tp.raw._t._sleep = sleep
            tp.raw._t._gate._until = 0.0
            with pytest.raises(RateLimitError):
                tp.destinations.list()
            return slept
        finally:
            rl.time.monotonic = original

    REAL_429 = {
        "retry-after": "5",
        "RateLimit-Limit": "300",
        "RateLimit-Remaining": "0",
        "RateLimit-Reset": "60",
    }

    def test_the_total_never_exceeds_the_cap(self):
        slept = self._run(120.0, self.REAL_429)
        assert sum(slept) <= 120.0 + 0.01, f"blocked {sum(slept):.0f}s under a 120s cap"

    def test_a_smaller_cap_binds_harder(self):
        slept = self._run(30.0, self.REAL_429)
        assert sum(slept) <= 30.0 + 0.01, f"blocked {sum(slept):.0f}s under a 30s cap"

    def test_it_still_waits_when_there_is_budget(self):
        # The cap must bound the feature, not disable it.
        slept = self._run(120.0, self.REAL_429)
        assert slept, "stopped waiting altogether"
        assert sum(slept) > 5.0, "only paid the gate, never the window"

    def test_no_meaningless_micro_sleeps(self):
        # Floating-point residue was producing a trailing sleep of ~1e-14.
        slept = self._run(120.0, self.REAL_429)
        assert all(s > 0.001 for s in slept), slept


class TestWaitersDoNotWakeAsOne:
    """The gate exists for concurrency, and nothing measured concurrency.

    Every other test here is sequential, so the one property the gate is for
    -- N waiters released without re-tripping the limit together -- was never
    asserted. Both release paths are covered, because the spent-window one had
    no spread at all: ten waiters derived the same deadline from the same
    observed_at and left inside the same millisecond, the tightest burst in
    the client, on the branch that exists to avoid a 429.
    """

    def test_gate_waiters_are_spread(self):
        gate = rl.Gate()
        gate.close_for(5.0)
        waits = [gate.wait_seconds() for _ in range(20)]
        assert len(set(waits)) > 1, "every waiter would wake at the same instant"
        assert all(5.0 <= w < 5.3 for w in waits), waits

    def test_the_spent_window_path_is_spread_too(self):
        # Against a FROZEN clock. With a live one `left` varies by itself as
        # time passes between runs, so the set is distinct with or without
        # spread and the test measures nothing -- the same vacuity that hid
        # the missing spread here in the first place.
        original = rl.time.monotonic
        rl.time.monotonic = lambda: 1000.0
        try:
            seen = set()
            for _ in range(20):
                slept: list[float] = []

                def handler(request):
                    return httpx.Response(
                        200,
                        headers={**REST, "RateLimit-Remaining": "0", "RateLimit-Reset": "7"},
                        json={"destinations": []},
                    )

                tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False)
                tp.raw._t._sleep = slept.append
                tp.destinations.list()
                tp.destinations.list()
                if slept:
                    seen.add(round(slept[0], 6))
            assert len(seen) > 1, f"all waiters left at the same instant: {seen}"
            assert all(7.0 <= w < 7.3 for w in seen), seen
        finally:
            rl.time.monotonic = original

    def test_the_spread_is_bounded(self):
        # It must not be mistaken for the wait itself.
        gate = rl.Gate()
        gate.close_for(1.0)
        assert max(gate.wait_seconds() for _ in range(50)) < 1.3
