"""Reading the two budgets, and staying inside them.

The SDK used to read exactly one header, `Retry-After`, and only after a 429
had already happened. It could tell you that you had run out, never that you
were about to. These cover the three things that changed: the figures are
read, absence is not confused with zero, and the wait a 429 imposes is taken
ONCE for the whole client rather than once per in-flight request.
"""

import time

import httpx

from themeparks import RateLimits, RetryConfig, ThemeParks
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


class TestAbsenceIsNotZero:
    def test_unknown_remaining_is_not_exhausted(self):
        # Anonymous responses carry no figures at all, because they are
        # publicly cacheable and the figures are per-caller. Reading that as
        # "nothing left" would stall every anonymous client permanently.
        assert RateLimit().exhausted is False

    def test_zero_remaining_is_exhausted(self):
        assert RateLimit(remaining=0).exhausted is True

    def test_reset_counts_down_from_when_it_was_read(self):
        # `reset` is relative and frozen at observed_at. Using it later
        # without ageing it is how a client waits far longer than it needs to.
        meter = RateLimit(reset=60, observed_at=time.monotonic() - 50)
        left = meter.seconds_until_reset()
        assert left is not None
        assert 9.0 <= left <= 11.0

    def test_an_expired_window_never_reports_negative(self):
        meter = RateLimit(reset=5, observed_at=time.monotonic() - 100)
        assert meter.seconds_until_reset() == 0.0

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
        assert 0 < slept[0] <= 7.0

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
