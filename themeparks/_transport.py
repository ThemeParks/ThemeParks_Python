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


class SyncTransport:
    def __init__(
        self,
        *,
        client: httpx.Client,
        base_url: str,
        user_agent: str,
        retry: RetryConfig,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._retry = retry
        self._sleep = sleep

    def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        while True:
            try:
                response = self._client.get(
                    path,
                    headers={"user-agent": self._user_agent, "accept": "application/json"},
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

            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
            ):
                ra = _parse_retry_after(response.headers.get("retry-after"))
                self._sleep(ra if ra is not None else _backoff(attempt))
                attempt += 1
                continue
            if status == _STATUS_TOO_MANY_REQUESTS:
                raise RateLimitError(
                    _format_error_message(status, response.reason_phrase, body),
                    status=status,
                    body=body,
                    url=url,
                    retry_after=_parse_retry_after(response.headers.get("retry-after")),
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
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        user_agent: str,
        retry: RetryConfig,
        sleep: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._retry = retry
        self._sleep: Callable[..., Awaitable[None]] = sleep if sleep is not None else asyncio.sleep

    async def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        while True:
            try:
                response = await self._client.get(
                    path,
                    headers={"user-agent": self._user_agent, "accept": "application/json"},
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

            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
            ):
                ra = _parse_retry_after(response.headers.get("retry-after"))
                await self._sleep(ra if ra is not None else _backoff(attempt))
                attempt += 1
                continue
            if status == _STATUS_TOO_MANY_REQUESTS:
                raise RateLimitError(
                    _format_error_message(status, response.reason_phrase, body),
                    status=status,
                    body=body,
                    url=url,
                    retry_after=_parse_retry_after(response.headers.get("retry-after")),
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
