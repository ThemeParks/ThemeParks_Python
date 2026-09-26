import time
from email.utils import formatdate

import httpx
import pytest

from themeparks import APIError, NetworkError, RateLimitError, TimeoutError
from themeparks._transport import RetryConfig, SyncTransport, _parse_body, _parse_retry_after


def make_transport(handler, *, retry_max=0, on_429=True) -> SyncTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    return SyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="test/1",
        retry=RetryConfig(max_retries=retry_max, respect_429=on_429),
        sleep=lambda _: None,
    )


def test_get_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler)
    assert t.get("/destinations") == {"ok": True}


def test_api_error_on_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"err": "nope"})

    t = make_transport(handler)
    with pytest.raises(APIError) as ei:
        t.get("/entity/missing")
    assert ei.value.status == 404
    assert ei.value.body == {"err": "nope"}
    # Message now includes status, reason, and a body excerpt.
    msg = str(ei.value)
    assert "404" in msg
    assert "nope" in msg


def test_api_error_message_includes_json_error_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad id"})

    t = make_transport(handler)
    with pytest.raises(APIError) as ei:
        t.get("/x")
    assert "bad id" in str(ei.value)


def test_api_error_message_truncates_long_body():
    long_body = "x" * 500

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, content=long_body.encode(), headers={"content-type": "text/plain"}
        )

    t = make_transport(handler, retry_max=0)
    with pytest.raises(APIError) as ei:
        t.get("/x")
    msg = str(ei.value)
    assert msg.endswith("...")
    assert len(msg) < 500


def test_api_error_message_omits_body_when_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    t = make_transport(handler)
    with pytest.raises(APIError) as ei:
        t.get("/x")
    # No trailing ": <body>" segment when body is absent.
    assert ":" not in str(ei.value)


def test_rate_limit_after_exhaustion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3"})

    t = make_transport(handler, retry_max=0, on_429=True)
    with pytest.raises(RateLimitError) as ei:
        t.get("/x")
    assert ei.value.retry_after == 3.0


def test_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3, on_429=True)
    assert t.get("/x") == {"ok": True}
    assert calls["n"] == 2


def test_retries_5xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3)
    assert t.get("/x") == {"ok": True}
    assert calls["n"] == 3


def test_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("econnreset")

    t = make_transport(handler, retry_max=0)
    with pytest.raises(NetworkError):
        t.get("/x")


def test_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    t = make_transport(handler, retry_max=0)
    with pytest.raises(TimeoutError):
        t.get("/x")


def test_retries_network_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3)
    assert t.get("/x") == {"ok": True}
    assert calls["n"] == 2


def test_retry_after_http_date_header_is_parsed():

    # Numeric
    assert _parse_retry_after("5") == 5.0
    # None
    assert _parse_retry_after(None) is None
    # HTTP-date (in the past -> clamped to 0.0)
    # A date in the past is not a wait. It used to come back as 0.0, and only
    # None reaches the exponential backoff, so 0.0 meant no wait at all:
    # four requests in 3ms against a server that had just said 429.
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    # Unparseable
    assert _parse_retry_after("not-a-date-ever") is None


def test_parse_body_non_json_returns_text():

    r = httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
    assert _parse_body(r) == "hello"


def test_parse_body_malformed_json_returns_none():

    r = httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"})
    assert _parse_body(r) is None


class TestRetryAfterCap:
    """A history 429 asks for most of an hour. We do not sleep through it.

    Before this cap the transport honoured any Retry-After up to max_retries
    times, so a spent history budget parked the process for roughly two and a
    half hours with no output. That is indistinguishable from a hang, and it
    made BudgetExhaustedError - the whole point of which is to let a backfill
    checkpoint instead of blocking - effectively unreachable.
    """

    def _transport(self, slept, calls, retry):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            headers = {} if self.retry_after is None else {"retry-after": self.retry_after}
            return httpx.Response(429, headers=headers, json={})

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
        return SyncTransport(
            client=client,
            base_url="https://x/v1",
            user_agent="test/1",
            retry=retry,
            sleep=slept.append,
        )

    def _run(self, retry_after, retry=None):
        self.retry_after = retry_after
        slept: list[float] = []
        calls: list[object] = []
        transport = self._transport(slept, calls, retry or RetryConfig())
        with pytest.raises(RateLimitError) as caught:
            transport.get("/anything")
        return caught.value, slept, calls

    def test_a_long_wait_is_not_slept_through(self):
        error, slept, calls = self._run("3000")
        assert slept == []
        assert len(calls) == 1
        # The caller still gets the number, so it can schedule its own return.
        assert error.retry_after == 3000.0

    def test_a_short_wait_is_still_honoured(self):
        _, slept, calls = self._run("5")
        # Jittered: the gate is shared, so without a little spread every
        # waiter would wake at the same instant and re-trip the limit
        # together. One wait per retry, taken once, never doubled.
        assert len(slept) == 3
        assert all(5.0 <= s < 5.3 for s in slept), slept
        assert len(calls) == 4

    def test_the_cap_is_configurable_and_bounds_the_whole_call(self):
        # This used to assert three sleeps of 3000s: 9000 seconds of blocking
        # under a 3600s cap, because the cap was checked per leg and never
        # against the total. The cap is a per-CALL budget now, so the sum is
        # what it bounds.
        _, slept, _ = self._run("3000", RetryConfig(max_retry_after=3600.0))
        assert sum(slept) <= 3600.0 + 0.01, slept
        assert slept, "stopped waiting altogether"
        assert slept[0] >= 3000.0

    def test_no_retry_after_header_still_backs_off(self):
        # The cap is about the server's stated wait. With no header we fall
        # back to our own backoff, which was never the problem.
        _, slept, _ = self._run(None)
        assert len(slept) == 3
        assert all(s > 0 for s in slept)


class TestRetryAfterNeverMeansNoWait:
    """`None` and `0` are different answers, and conflating them hammers us.

    Only `None` reaches the exponential backoff. A header that parsed to zero
    -- `Retry-After: 0`, which RFC 9110 permits, or a negative, or an
    already-past date -- therefore produced no wait at all. Measured before
    this: four requests in 3ms against a server actively refusing them, and
    204 requests a second across ten threads.
    """

    @pytest.mark.parametrize("raw", ["0", "-5", "0.0", "Wed, 21 Oct 2015 07:28:00 GMT"])
    def test_a_non_positive_wait_is_no_wait_at_all(self, raw):
        assert _parse_retry_after(raw) is None

    @pytest.mark.parametrize("raw", ["1", "45", "0.5"])
    def test_a_real_wait_is_honoured(self, raw):
        assert _parse_retry_after(raw) == float(raw)

    def test_a_spin_falls_back_to_backoff(self):
        # The behaviour that matters: a zero must not skip the backoff.
        slept: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"retry-after": "0"}, json={})

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
        transport = SyncTransport(
            client=client,
            base_url="https://x/v1",
            user_agent="test/1",
            retry=RetryConfig(max_retries=3),
            sleep=slept.append,
        )
        with pytest.raises(RateLimitError):
            transport.get("/anything")
        assert len(slept) == 3
        assert all(s > 0 for s in slept), slept
        # And it grows, rather than retrying at a fixed rate.
        assert slept[-1] > slept[0]

    def test_a_naive_http_date_is_read_as_utc(self):
        # parsedate_to_datetime returns a NAIVE datetime for the RFC 5322
        # `-0000` form, and .timestamp() then read it as local time: wrong by
        # the host's UTC offset, and negative enough to become the spin above.
        naive = _parse_retry_after(formatdate(time.time() + 120))
        gmt = _parse_retry_after(formatdate(time.time() + 120, usegmt=True))
        assert naive is not None and gmt is not None
        assert abs(naive - gmt) < 2.0, (naive, gmt)
