import httpx
import pytest

from themeparks import APIError, NetworkError, RateLimitError, TimeoutError
from themeparks._transport import AsyncTransport, RetryConfig


async def _noop_sleep(_):
    return None


def make_transport(handler, *, retry_max=0, on_429=True) -> AsyncTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    return AsyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="test/1",
        retry=RetryConfig(max_retries=retry_max, respect_429=on_429),
        sleep=_noop_sleep,
    )


async def test_async_get_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler)
    assert await t.get("/destinations") == {"ok": True}


async def test_async_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"err": "nope"})

    t = make_transport(handler)
    with pytest.raises(APIError) as ei:
        await t.get("/entity/missing")
    assert ei.value.status == 404
    assert ei.value.body == {"err": "nope"}
    msg = str(ei.value)
    assert "404" in msg and "nope" in msg


async def test_async_retries_5xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3)
    assert await t.get("/x") == {"ok": True}
    assert calls["n"] == 3


async def test_async_rate_limit_after_exhaustion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3"})

    t = make_transport(handler, retry_max=0, on_429=True)
    with pytest.raises(RateLimitError) as ei:
        await t.get("/x")
    assert ei.value.retry_after == 3.0


async def test_async_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("econnreset")

    t = make_transport(handler, retry_max=0)
    with pytest.raises(NetworkError):
        await t.get("/x")


async def test_async_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    t = make_transport(handler, retry_max=0)
    with pytest.raises(TimeoutError):
        await t.get("/x")


async def test_async_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3, on_429=True)
    assert await t.get("/x") == {"ok": True}
    assert calls["n"] == 2


async def test_async_retries_network_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json={"ok": True})

    t = make_transport(handler, retry_max=3)
    assert await t.get("/x") == {"ok": True}
    assert calls["n"] == 2


class TestRetryAfterCapAsync:
    """The async transport must cap the same way. See the sync sibling.

    An async backfill that sleeps for most of an hour inside a single await is
    worse than the sync one, not better: it holds the task and gives the event
    loop nothing to say about it.
    """

    def _run_setup(self, retry_after, retry):
        slept: list[float] = []
        calls: list[object] = []

        async def sleep(seconds):
            slept.append(seconds)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            headers = {} if retry_after is None else {"retry-after": retry_after}
            return httpx.Response(429, headers=headers, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
        transport = AsyncTransport(
            client=client,
            base_url="https://x/v1",
            user_agent="test/1",
            retry=retry,
            sleep=sleep,
        )
        return transport, slept, calls

    async def test_a_long_wait_is_not_slept_through(self):
        transport, slept, calls = self._run_setup("3000", RetryConfig())
        with pytest.raises(RateLimitError) as caught:
            await transport.get("/anything")
        assert slept == []
        assert len(calls) == 1
        assert caught.value.retry_after == 3000.0

    async def test_a_short_wait_is_still_honoured(self):
        transport, slept, calls = self._run_setup("5", RetryConfig())
        with pytest.raises(RateLimitError):
            await transport.get("/anything")
        assert slept == [5.0, 5.0, 5.0]
        assert len(calls) == 4
