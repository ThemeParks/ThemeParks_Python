"""Sync and async HTTP transport for the ThemeParks SDK."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timezone
from typing import Any

import httpx

from themeparks._errors import APIError, NetworkError, RateLimitError, TimeoutError
from themeparks._ratelimit import Gate, RateLimits, read_rate_limits

_STATUS_TOO_MANY_REQUESTS = 429
_STATUS_SERVER_ERROR = 500
_ERROR_BODY_EXCERPT_LIMIT = 200
_ERROR_MESSAGE_LIMIT = 300
#: Below this, a computed wait is floating-point residue rather than a wait.
_MIN_SLEEP_SECONDS = 0.001
#: Spread applied to a synchronised release, so waiters do not wake as one.
_SPREAD_SECONDS = 0.25


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
    #: Wait out a window the server has already told us is spent.
    #:
    #: When a response says `remaining: 0`, the next request is a guaranteed
    #: 429 that also costs us a unit of the caller's budget to refuse. Waiting
    #: for the reset it advertised is strictly better than sending it. Off
    #: turns the client back into a purely reactive one.
    respect_remaining: bool = True


def _parse_retry_after(raw: str | None) -> float | None:
    """Seconds to wait, or None when the header gives us nothing usable.

    NONE AND ZERO ARE DIFFERENT ANSWERS, and conflating them turned the client
    into a hammer. Only `None` reaches the exponential backoff, so a header
    that parsed to 0 -- which `Retry-After: 0` is, legally, per RFC 9110, and
    which a negative or already-past date also produces -- meant no wait at
    all. Measured: four requests in 3ms against a server that had just said
    429, where an absent header correctly took 1962ms. Ten threads made that
    204 requests a second at a server actively refusing them.

    So a non-positive wait is not a wait, and we say None.
    """
    if raw is None:
        return None
    seconds: float | None
    try:
        seconds = float(raw)
    except ValueError:
        seconds = _parse_http_date_delta(raw)
    if seconds is None or seconds <= 0:
        return None
    return seconds


def _parse_http_date_delta(raw: str) -> float | None:
    """An HTTP-date Retry-After, as seconds from now.

    RFC 9110 requires the IMF-fixdate (GMT) form, but RFC 5322 `-0000` and a
    bare date both appear in the wild, and `parsedate_to_datetime` returns a
    NAIVE datetime for them. `.timestamp()` then reads it as local time, so on
    a host an hour off UTC the answer was wrong by exactly that hour -- and
    because it came out negative it became 0, landing in the no-backoff spin
    above. Assume UTC when the sender did not say.
    """
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() - time.time()


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
        self.rate_limit = RateLimits()
        self._gate = Gate()

    def _hold(self, budget: float) -> float:
        """Wait before sending, if we already know this request would fail.

        Returns how long it slept, so the caller can keep a running total. The
        TOTAL is what `max_retry_after` bounds, not each leg: the gate wait and
        the spent-window wait are both self-initiated holds, and they stack.
        Measured before this budget existed: a 429 carrying `Retry-After: 5`
        and `RateLimit-Reset: 60` slept 5 then 55, three times over -- 180
        seconds inside one call whose cap was 120. Each leg was under the cap,
        so the per-leg check never fired, and the promise the cap makes was
        reachable around.

        Two reasons to hold, and they are different. The GATE is a 429 the
        server has already issued to this caller: the wait belongs to them,
        not to whichever request met it, so it is shared and taken once. The
        REMAINING check is a window the server told us is spent -- sending
        into it is a guaranteed 429 that also costs a unit of budget to
        refuse, so waiting for the advertised reset is strictly better.

        A remaining we were never told is not a spent one. Anonymous
        responses carry no figures at all, so an unknown must never hold.
        """
        spent = 0.0
        # Re-read the gate after waiting. It slept once and returned, so a
        # waiter that woke while someone else's 429 had pushed the gate
        # further out sent anyway. Only loop when the deadline actually
        # MOVED: re-reading unconditionally spins against any clock that does
        # not advance.
        while True:
            before = self._gate.deadline
            wait = min(self._gate.wait_seconds(), budget - spent)
            if wait <= _MIN_SLEEP_SECONDS:
                break
            self._sleep(wait)
            spent += wait
            if self._gate.deadline <= before:
                break
        if not self._retry.respect_remaining:
            return spent
        for meter in (self.rate_limit.rest, self.rate_limit.history):
            if not meter.exhausted:
                continue
            left = meter.seconds_until_reset()
            if left is None or left <= 0 or left > self._retry.max_retry_after:
                # Past the cap we do not sit on it: the caller gets the 429
                # and its Retry-After, and can decide. Same rule the retry
                # path follows.
                continue
            # Jittered like the gate. Without it every waiter derived `left`
            # from the same observed_at and woke at the same absolute
            # instant -- the tightest burst in the client, on the very branch
            # that exists to avoid a 429.
            left = min(left + random.random() * _SPREAD_SECONDS, budget - spent)
            if left <= _MIN_SLEEP_SECONDS:
                break
            self._sleep(left)
            spent += left
        return spent

    def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        # One budget for the whole call, because that is what the cap promises.
        budget = self._retry.max_retry_after
        while True:
            budget -= self._hold(budget)
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

            self.rate_limit = read_rate_limits(response.headers, self.rate_limit)

            if response.is_success:
                return _parse_body(response)

            body = _parse_body(response)
            status = response.status_code

            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and retry_after is not None
                and not _wait_too_long(retry_after, self._retry)
            ):
                # The wait belongs to the CALLER, not to whichever request met
                # it, so it goes on the shared gate and _hold() serves it once.
                #
                # respect_429=False means "do not wait on a 429", so it must
                # gate the gate too. Without this the caller got the exception
                # they asked for and then their NEXT call silently blocked,
                # which is an opt-out that does not opt out.
                #
                # Past the cap the gate is left OPEN on purpose: we raise
                # instead, and blocking the caller's next call for most of an
                # hour is the opposite of letting them checkpoint and resume.
                self._gate.close_for(retry_after)
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
                and not _wait_too_long(retry_after, self._retry)
            ):
                # No sleep here: the gate above holds the wait and _hold()
                # at the top of the loop serves it once. Paying it here too
                # would double every backoff, and ten concurrent requests
                # would each pay their own and then retry in unison.
                if retry_after is None:
                    self._sleep(_backoff(attempt))
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
        self.rate_limit = RateLimits()
        self._gate = Gate()

    async def _hold(self, budget: float) -> float:
        """Asynchronous mirror of :meth:`SyncTransport._hold`."""
        spent = 0.0
        # Re-read the gate after waiting. It slept once and returned, so a
        # waiter that woke while someone else's 429 had pushed the gate
        # further out sent anyway. Only loop when the deadline actually
        # MOVED: re-reading unconditionally spins against any clock that does
        # not advance.
        while True:
            before = self._gate.deadline
            wait = min(self._gate.wait_seconds(), budget - spent)
            if wait <= _MIN_SLEEP_SECONDS:
                break
            await self._sleep(wait)
            spent += wait
            if self._gate.deadline <= before:
                break
        if not self._retry.respect_remaining:
            return spent
        for meter in (self.rate_limit.rest, self.rate_limit.history):
            if not meter.exhausted:
                continue
            left = meter.seconds_until_reset()
            if left is None or left <= 0 or left > self._retry.max_retry_after:
                continue
            # Jittered like the gate. Without it every waiter derived `left`
            # from the same observed_at and woke at the same absolute
            # instant -- the tightest burst in the client, on the very branch
            # that exists to avoid a 429.
            left = min(left + random.random() * _SPREAD_SECONDS, budget - spent)
            if left <= _MIN_SLEEP_SECONDS:
                break
            await self._sleep(left)
            spent += left
        return spent

    async def get(self, path: str) -> Any:
        url = self._base_url + path
        attempt = 0
        budget = self._retry.max_retry_after
        while True:
            budget -= await self._hold(budget)
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

            self.rate_limit = read_rate_limits(response.headers, self.rate_limit)

            if response.is_success:
                return _parse_body(response)

            body = _parse_body(response)
            status = response.status_code

            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and retry_after is not None
                and not _wait_too_long(retry_after, self._retry)
            ):
                # The wait belongs to the CALLER, not to whichever request met
                # it, so it goes on the shared gate and _hold() serves it once.
                #
                # respect_429=False means "do not wait on a 429", so it must
                # gate the gate too. Without this the caller got the exception
                # they asked for and then their NEXT call silently blocked,
                # which is an opt-out that does not opt out.
                #
                # Past the cap the gate is left OPEN on purpose: we raise
                # instead, and blocking the caller's next call for most of an
                # hour is the opposite of letting them checkpoint and resume.
                self._gate.close_for(retry_after)
            if (
                status == _STATUS_TOO_MANY_REQUESTS
                and self._retry.respect_429
                and attempt < self._retry.max_retries
                and not _wait_too_long(retry_after, self._retry)
            ):
                # No sleep here: the gate above holds the wait and _hold()
                # at the top of the loop serves it once. Paying it here too
                # would double every backoff, and ten concurrent requests
                # would each pay their own and then retry in unison.
                if retry_after is None:
                    await self._sleep(_backoff(attempt))
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
