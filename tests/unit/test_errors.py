from themeparks import (
    APIError,
    NetworkError,
    RateLimitError,
    ThemeParksError,
)
from themeparks import (
    TimeoutError as ThemeParksTimeoutError,
)


def test_base_is_exception():
    e = ThemeParksError("boom")
    assert isinstance(e, Exception)
    assert str(e) == "boom"


def test_api_error_carries_status_body_url():
    e = APIError("404 Not Found", status=404, body={"err": "x"}, url="/entity/z")
    assert isinstance(e, ThemeParksError)
    assert e.status == 404
    assert e.body == {"err": "x"}
    assert e.url == "/entity/z"


def test_rate_limit_error_extends_api_error():
    e = RateLimitError(
        "rate limited", status=429, body=None, url="/x", retry_after=1.5
    )
    assert isinstance(e, APIError)
    assert e.retry_after == 1.5


def test_network_error_wraps_cause():
    cause = RuntimeError("econnreset")
    e = NetworkError("network failure")
    e.__cause__ = cause
    assert isinstance(e, ThemeParksError)
    assert e.__cause__ is cause


def test_timeout_error_is_themeparks_error():
    e = ThemeParksTimeoutError("timed out")
    assert isinstance(e, ThemeParksError)
