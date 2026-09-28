"""The async client's half of the rate-limit feature.

It had NO tests. Every assertion in test_ratelimit.py drives the sync client,
so the async transport's hold, the async caching wrapper's `rate_limit`
property and AsyncThemeParks.rate_limit were all reachable only by reading.

That matters here specifically: the caching wrapper with no `rate_limit` to
forward is the exact bug that crashed on the first real call against
production. It was fixed in both wrappers and tested in one.
"""

import httpx
import pytest

from themeparks import AsyncThemeParks, RateLimitError, RetryConfig

REST = {
    "RateLimit-Limit": "300",
    "RateLimit-Policy": "300;w=60",
    "RateLimit-Remaining": "299",
    "RateLimit-Reset": "60",
}


def _client(headers, *, cache=True, retry=None, status=200):
    def handler(request):
        return httpx.Response(status, headers=headers, json={"destinations": []})

    kwargs = {"transport": httpx.MockTransport(handler), "cache": cache}
    if retry is not None:
        kwargs["retry"] = retry
    return AsyncThemeParks(**kwargs)


class TestAsyncReadsTheMeters:
    async def test_a_real_call_records_them(self):
        tp = _client(REST, cache=False)
        await tp.destinations.list()
        assert tp.rate_limit.rest.remaining == 299

    async def test_it_survives_the_caching_wrapper(self):
        # The sync wrapper's missing property raised AttributeError on the
        # first production call. The async one is the same shape.
        tp = _client(REST)
        await tp.destinations.list()
        assert tp.rate_limit.rest.remaining == 299

    async def test_before_any_call_everything_is_unknown(self):
        tp = _client(REST, cache=False)
        assert tp.rate_limit.rest.limit is None
        assert tp.rate_limit.rest.exhausted is False

    async def test_a_cached_response_says_nothing(self):
        tp = _client({**REST, "Age": "1713"}, cache=False)
        await tp.destinations.list()
        assert tp.rate_limit.rest.remaining is None


class TestAsyncHolds:
    async def _slept(self, headers, retry=None, status=200):
        slept: list[float] = []
        tp = _client(headers, cache=False, retry=retry, status=status)

        async def sleep(seconds):
            slept.append(seconds)

        tp.raw._t._sleep = sleep
        return tp, slept

    async def test_a_spent_window_is_waited_out(self):
        tp, slept = await self._slept({**REST, "RateLimit-Remaining": "0", "RateLimit-Reset": "7"})
        await tp.destinations.list()
        await tp.destinations.list()
        assert slept, "walked into a window the server said was spent"
        # Upper bound allows the spread now applied to this path: without it
        # every waiter woke at the same absolute instant.
        assert 0 < slept[0] <= 7.0 + 0.25

    async def test_an_unknown_remaining_never_holds(self):
        tp, slept = await self._slept({"content-type": "application/json"})
        await tp.destinations.list()
        await tp.destinations.list()
        assert slept == []

    async def test_respect_remaining_can_be_turned_off(self):
        tp, slept = await self._slept(
            {**REST, "RateLimit-Remaining": "0", "RateLimit-Reset": "7"},
            retry=RetryConfig(respect_remaining=False),
        )
        await tp.destinations.list()
        await tp.destinations.list()
        assert slept == []

    async def test_respect_429_false_never_sleeps_even_on_a_later_call(self):
        tp, slept = await self._slept(
            {"retry-after": "45"}, retry=RetryConfig(respect_429=False), status=429
        )
        for _ in range(3):
            with pytest.raises(RateLimitError):
                await tp.destinations.list()
        assert slept == [], "opted out of 429 waiting and waited anyway"

    async def test_the_gate_still_holds_for_callers_who_want_it(self):
        tp, slept = await self._slept(
            {"retry-after": "45"}, retry=RetryConfig(max_retries=0), status=429
        )
        for _ in range(2):
            with pytest.raises(RateLimitError):
                await tp.destinations.list()
        assert slept
