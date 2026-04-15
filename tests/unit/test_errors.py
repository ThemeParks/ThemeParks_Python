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
    try:
        raise NetworkError("network failure") from RuntimeError("econnreset")
    except NetworkError as e:
        assert isinstance(e.__cause__, RuntimeError)
        assert str(e.__cause__) == "econnreset"


def test_timeout_error_is_themeparks_error():
    e = ThemeParksTimeoutError("timed out")
    assert isinstance(e, ThemeParksError)


def test_api_error_repr_includes_status_and_url():
    e = APIError("boom", status=500, body=None, url="/x")
    assert repr(e) == "APIError(status=500, url='/x')"


def test_rate_limit_error_repr_includes_retry_after():
    e = RateLimitError(
        "rate limited", status=429, body=None, url="/x", retry_after=5.0
    )
    assert repr(e) == "RateLimitError(status=429, url='/x', retry_after=5.0)"


def test_rate_limit_error_repr_handles_missing_retry_after():
    e = RateLimitError("rate limited", status=429, body=None, url="/x")
    assert repr(e) == "RateLimitError(status=429, url='/x', retry_after=None)"
