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
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    # Unparseable
    assert _parse_retry_after("not-a-date-ever") is None


def test_parse_body_non_json_returns_text():

    r = httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
    assert _parse_body(r) == "hello"


def test_parse_body_malformed_json_returns_none():

    r = httpx.Response(
        200, content=b"{not json", headers={"content-type": "application/json"}
    )
    assert _parse_body(r) is None
