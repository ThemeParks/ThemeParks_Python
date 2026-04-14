import httpx
import pytest

from themeparks import APIError, NetworkError, RateLimitError, TimeoutError
from themeparks._transport import RetryConfig, SyncTransport


def make_transport(handler, *, retry_max=0, on_429=True) -> SyncTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    return SyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="test/1",
        retry=RetryConfig(max_attempts=retry_max, respect_429=on_429),
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
