"""Error classes for the ThemeParks SDK."""
from __future__ import annotations

from typing import Any, Optional


class ThemeParksError(Exception):
    """Base class for all errors raised by the ThemeParks SDK."""


class APIError(ThemeParksError):
    """Raised when the API responds with a non-2xx status."""

    def __init__(self, message: str, *, status: int, body: Any, url: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status}, url={self.url!r})"


class RateLimitError(APIError):
    """Raised when the API responds 429. `retry_after` is seconds, if present."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        body: Any,
        url: str,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, status=status, body=body, url=url)
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status={self.status}, url={self.url!r}, "
            f"retry_after={self.retry_after})"
        )


class NetworkError(ThemeParksError):
    """Raised when the underlying transport fails."""


class TimeoutError(ThemeParksError):
    """Raised when a request exceeds the configured timeout."""
