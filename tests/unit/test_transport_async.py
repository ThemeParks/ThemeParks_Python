import httpx
import pytest

from themeparks import APIError
from themeparks._transport import AsyncTransport, RetryConfig


async def _noop_sleep(_):
    return None


def make_transport(handler, *, retry_max=0, on_429=True) -> AsyncTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    return AsyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="test/1",
        retry=RetryConfig(max_attempts=retry_max, respect_429=on_429),
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
