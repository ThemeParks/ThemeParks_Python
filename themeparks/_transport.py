"""Sync and async HTTP transport for the ThemeParks SDK."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from themeparks._errors import APIError, NetworkError, RateLimitError, TimeoutError

_STATUS_TOO_MANY_REQUESTS = 429
_STATUS_SERVER_ERROR = 500
_ERROR_BODY_EXCERPT_LIMIT = 200
_ERROR_MESSAGE_LIMIT = 300


@dataclass
class RetryConfig:
    max_retries: int = 3
    respect_429: bool = True
    #: Longest `Retry-After` this client will sleep through, in seconds.
    #:
    #: A REST 429 asks for seconds and is worth waiting out. A HISTORY 429 is
    #: a different animal: that budget is hourly, so a spent one can ask for
    #: most of an hour, and honouring it up to `max_retries` times means a
    #: process that sits silent for hours and looks hung. Past this cap we do
    #: not sleep at all, and raise `RateLimitError` carrying `retry_after` so
    #: the caller can checkpoint and come back.
    max_retry_after: float = 120.0


def _parse_retry_after(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        return max(0.0, parsed.timestamp() - time.time())
    except Exception:
        return None


def _wait_too_long(retry_after: float | None, retry: RetryConfig) -> bool:
    """True when the server's wait is longer than this client will sleep for."""
    return retry_after is not None and retry_after > retry.max_retry_after


def _backoff(attempt: int) -> float:
    base = 0.25 * (2**attempt)
    jittered: float = base + random.random() * base * 0.25
    return min(jittered, 5.0)


def _format_error_message(status: int, reason: str, body: Any) -> str:
    """Build a human-useful error message from an HTTP response.

    Includes a body excerpt when present so callers see *why* the request
    failed without having to inspect ``exc.body`` manually. Dict bodies
    with an ``"error"`` key are formatted specially; other bodies are
    stringified and truncated to 200 characters.
    """
    if body is None or body == "":
        return f"{status} {reason}"
    if isinstance(body, dict) and "error" in body:
        return f"{status} {reason}: {body['error']}"[:_ERROR_MESSAGE_LIMIT]
    body_str = str(body)
    if len(body_str) > _ERROR_BODY_EXCERPT_LIMIT:
        body_str = body_str[:_ERROR_BODY_EXCERPT_LIMIT] + "..."
    return f"{status} {reason}: {body_str}"


def _parse_body(response: httpx.Response) -> Any:
    ct = response.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            return response.json()
        except Exception:
            return None
    try:
        return response.text
    except Exception:
        return None


def _headers(user_agent: str, api_key: str | None) -> dict[str, str]:
    """Request headers, with the API key when one was supplied.

    The SDK could not send a key at all until 2026-09-23, which meant the
    official library could reach only the anonymous window: seven days of
    history and the unauthenticated rate limit. A paying customer had to drop
    to raw HTTP to use what they had bought.

    `x-api-key` is the header the API documents. Nothing here logs or repeats
    the value.
    """
    headers = {"user-agent": user_agent, "accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


class SyncTransport:
    def __init__(  # noqa: PLR0913
        self,
        *,
        client: httpx.Client,
        base_url: str,
        user_agent: str,
        retry: RetryConfig,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._retry = retry
        self._headers = _headers(user_agent, api_key)
        self._sleep = sleep

    def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        while True:
            try:
                response = self._client.get(
                    path,
                    headers=self._headers,
                )
            except httpx.TimeoutException as exc:
                raise TimeoutError(f"request to {url} timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self._retry.max_retries:
                    self._sleep(_backoff(attempt))
                    attempt += 1
                    continue
                raise NetworkError(f"network error calling {url}") from exc

            if response.is_success:
                return _parse_body(response)

            body = _parse_body(response)
            status = response.status_code

            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
                and not _wait_too_long(retry_after, self._retry)
            ):
                self._sleep(retry_after if retry_after is not None else _backoff(attempt))
                attempt += 1
                continue
            if status == _STATUS_TOO_MANY_REQUESTS:
                raise RateLimitError(
                    _format_error_message(status, response.reason_phrase, body),
                    status=status,
                    body=body,
                    url=url,
                    retry_after=retry_after,
                )
            if status >= _STATUS_SERVER_ERROR and attempt < self._retry.max_retries:
                self._sleep(_backoff(attempt))
                attempt += 1
                continue
            raise APIError(
                _format_error_message(status, response.reason_phrase, body),
                status=status,
                body=body,
                url=url,
            )


class AsyncTransport:
    def __init__(  # noqa: PLR0913
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        user_agent: str,
        retry: RetryConfig,
        api_key: str | None = None,
        sleep: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._retry = retry
        self._headers = _headers(user_agent, api_key)
        self._sleep: Callable[..., Awaitable[None]] = sleep if sleep is not None else asyncio.sleep

    async def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        while True:
            try:
                response = await self._client.get(
                    path,
                    headers=self._headers,
                )
            except httpx.TimeoutException as exc:
                raise TimeoutError(f"request to {url} timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self._retry.max_retries:
                    await self._sleep(_backoff(attempt))
                    attempt += 1
                    continue
                raise NetworkError(f"network error calling {url}") from exc

            if response.is_success:
                return _parse_body(response)

            body = _parse_body(response)
            status = response.status_code

            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
                and not _wait_too_long(retry_after, self._retry)
            ):
                await self._sleep(retry_after if retry_after is not None else _backoff(attempt))
                attempt += 1
                continue
            if status == _STATUS_TOO_MANY_REQUESTS:
                raise RateLimitError(
                    _format_error_message(status, response.reason_phrase, body),
                    status=status,
                    body=body,
                    url=url,
                    retry_after=retry_after,
                )
            if status >= _STATUS_SERVER_ERROR and attempt < self._retry.max_retries:
                await self._sleep(_backoff(attempt))
                attempt += 1
                continue
            raise APIError(
                _format_error_message(status, response.reason_phrase, body),
                status=status,
                body=body,
                url=url,
            )
