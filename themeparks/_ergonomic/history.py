"""Historical wait time data, without the paging loop.

WHY THIS LAYER EXISTS. The raw endpoints are honest but they hand the caller
four jobs: walk the `next` links, respect an hourly budget that is separate
from the per-minute one, know that asking a PARK returns a different shape
from asking a ride, and keep five years of rows out of memory. Every customer
who buys history writes the same loop, and most of them write it wrong: the
first version of our own monitor treated a 429 as "no data" and reported
all-clear for eight days.

So this module does the walking. `days()` and `changes()` are iterators that
page until the server stops offering a `next`, and they yield rows rather than
returning a list, because a resort's five years is not a list.

THE ONE THING THAT MATTERS MOST is that asking about a park uses the PARK
call. Both history endpoints answer a whole park in one request; asking ride by
ride costs 122 times more for the same data. A caller who passes a park id gets
the cheap path here without having to know the expensive one exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date as _date
from typing import Any, NamedTuple, Union

from themeparks._errors import RateLimitError
from themeparks._generated.models import (
    HistoryCoverageDocument,
    HistoryDailyEnvelope,
    HistoryDailyRow,
    HistoryEnvelope,
    HistoryParkCoverageDocument,
    HistoryParkDailyEnvelope,
    HistoryParkRawEnvelope,
    HistoryRow,
)
from themeparks._raw import AsyncRawClient, RawClient, _parse_daily_history

CoverageDocument = Union[HistoryCoverageDocument, HistoryParkCoverageDocument]
DailyEnvelope = Union[HistoryDailyEnvelope, HistoryParkDailyEnvelope]
RawEnvelope = Union[HistoryEnvelope, HistoryParkRawEnvelope]

# A history 429 can ask for a very long wait: the budget is hourly, so a spent
# one is up to an hour away from resetting. The transport will honour a
# Retry-After by sleeping, which is right for a 30-second REST limit and wrong
# for a 50-minute history one - a backfill that blocks for most of an hour
# looks identical to a hung process. Past this, we raise instead, so the caller
# can checkpoint and come back.
DEFAULT_MAX_WAIT_SECONDS = 120.0


class HistorySpan(NamedTuple):
    """The three dates a backfill needs, in one shape for parks and rides.

    A park's coverage document nests these under `summary` and an entity's
    carries them at the top level under different names, so without this
    every caller writes the same branch before they can ask their first
    question.

    `retrievable_through` is the end date to use. It is what YOUR key may
    retrieve, not what the archive holds, so a backfill bounded by it stops
    at the edge of the plan instead of walking into 403s.
    """

    archive_from: _date | None
    recorded_to: _date | None
    retrievable_through: _date | None


def _span(document: CoverageDocument) -> HistorySpan:
    summary = getattr(document, "summary", None)
    if summary is not None:
        return HistorySpan(summary.archiveFrom, summary.recordedTo, summary.retrievableThrough)
    return HistorySpan(
        document.firstRecordedAt,  # type: ignore[union-attr]
        document.lastRecordedAt,  # type: ignore[union-attr]
        document.retrievableThrough,  # type: ignore[union-attr]
    )


def _as_day(value: str | _date | None) -> str | None:
    """Accept a date object or an ISO day string; days here are park-local."""
    if value is None:
        return None
    if isinstance(value, _date):
        return value.isoformat()
    return value


class BudgetExhaustedError(RateLimitError):
    """The hourly history budget is spent and the wait is longer than allowed.

    Carries `retry_after` from the underlying 429, so a backfill can record
    where it got to and resume after that long rather than holding a process
    open waiting for a budget window to roll.
    """


def _reraise_if_too_long(exc: RateLimitError, max_wait: float) -> None:
    wait = exc.retry_after
    if wait is not None and wait > max_wait:
        raise BudgetExhaustedError(
            f"history budget exhausted; retry in {wait:.0f}s "
            f"(longer than max_wait={max_wait:.0f}s). Checkpoint and resume.",
            status=exc.status,
            body=exc.body,
            url=exc.url,
            retry_after=wait,
        ) from exc
    raise exc


def _daily_rows(envelope: DailyEnvelope) -> Iterator[tuple[str, HistoryDailyRow]]:
    """Yield (entity id, row). A park envelope carries many entities; an entity
    envelope carries its own rows, so both flatten to the same stream."""
    entities = getattr(envelope, "entities", None)
    if entities is not None:
        for entity in entities:
            for row in entity.days or []:
                yield (entity.id, row)
        return
    for row in getattr(envelope, "days", None) or []:
        yield (envelope.id, row)


def _raw_rows(envelope: RawEnvelope) -> Iterator[tuple[str, HistoryRow]]:
    entities = getattr(envelope, "entities", None)
    if entities is not None:
        for entity in entities:
            for row in entity.history or []:
                yield (entity.id, row)
        return
    for row in getattr(envelope, "history", None) or []:
        yield (envelope.id, row)


class HistoryApi:
    """History for one entity id, reached as ``tp.entity(id).history``."""

    def __init__(self, raw: RawClient, entity_id: str) -> None:
        self._raw = raw
        self._id = entity_id

    def coverage(self) -> CoverageDocument:
        """What history exists here, field by field. Ask this before a backfill."""
        return self._raw.get_entity_history_coverage(self._id)

    def span(self) -> HistorySpan:
        """The dates a backfill should run between, as a :class:`HistorySpan`.

        One call, and the same three fields whether this id is a park or a
        single ride.
        """
        return _span(self.coverage())

    def days(
        self,
        start: str | _date | None = None,
        end: str | _date | None = None,
        *,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> Iterator[tuple[str, HistoryDailyRow]]:
        """One summary row per park-local day, as (entity id, row).

        Pages automatically. Given a park id this uses the park call, which
        answers every entity in the park in one request.
        """
        envelope: DailyEnvelope | None = self._first_daily(start, end, max_wait)
        while envelope is not None:
            yield from _daily_rows(envelope)
            envelope = self._next_daily(envelope, max_wait)

    def _first_daily(
        self, start: str | _date | None, end: str | _date | None, max_wait: float
    ) -> DailyEnvelope:
        try:
            return self._raw.get_entity_history_daily(
                self._id, start=_as_day(start), end=_as_day(end)
            )
        except RateLimitError as exc:
            _reraise_if_too_long(exc, max_wait)
            raise

    def _next_daily(self, envelope: DailyEnvelope, max_wait: float) -> DailyEnvelope | None:
        nxt = getattr(envelope, "next", None)
        if not nxt:
            return None
        try:
            payload: Any = self._raw.get_path(nxt)
        except RateLimitError as exc:
            _reraise_if_too_long(exc, max_wait)
            raise
        return _parse_daily_history(payload)

    def changes(
        self,
        date: str | _date | None = None,
        *,
        start: str | _date | None = None,
        end: str | _date | None = None,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> Iterator[tuple[str, HistoryRow]]:
        """Every recorded change, as (entity id, row).

        A single day for a park, or up to 31 days for one entity. The caller
        does not have to know which cap applies: ask for what you want and the
        API answers or tells you the range is too long.
        """
        try:
            envelope = self._raw.get_entity_history(
                self._id, date=_as_day(date), start=_as_day(start), end=_as_day(end)
            )
        except RateLimitError as exc:
            _reraise_if_too_long(exc, max_wait)
            raise
        yield from _raw_rows(envelope)


class AsyncHistoryApi:
    """Asynchronous mirror of :class:`HistoryApi`."""

    def __init__(self, raw: AsyncRawClient, entity_id: str) -> None:
        self._raw = raw
        self._id = entity_id

    async def coverage(self) -> CoverageDocument:
        return await self._raw.get_entity_history_coverage(self._id)

    async def span(self) -> HistorySpan:
        """Asynchronous mirror of :meth:`HistoryApi.span`."""
        return _span(await self.coverage())

    async def days(
        self,
        start: str | _date | None = None,
        end: str | _date | None = None,
        *,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> AsyncIterator[tuple[str, HistoryDailyRow]]:
        try:
            envelope: DailyEnvelope | None = await self._raw.get_entity_history_daily(
                self._id, start=_as_day(start), end=_as_day(end)
            )
        except RateLimitError as exc:
            _reraise_if_too_long(exc, max_wait)
            raise
        while envelope is not None:
            for pair in _daily_rows(envelope):
                yield pair
            nxt = getattr(envelope, "next", None)
            if not nxt:
                return
            try:
                envelope = _parse_daily_history(await self._raw.get_path(nxt))
            except RateLimitError as exc:
                _reraise_if_too_long(exc, max_wait)
                raise

    async def changes(
        self,
        date: str | _date | None = None,
        *,
        start: str | _date | None = None,
        end: str | _date | None = None,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> AsyncIterator[tuple[str, HistoryRow]]:
        try:
            envelope = await self._raw.get_entity_history(
                self._id, date=_as_day(date), start=_as_day(start), end=_as_day(end)
            )
        except RateLimitError as exc:
            _reraise_if_too_long(exc, max_wait)
            raise
        for pair in _raw_rows(envelope):
            yield pair
