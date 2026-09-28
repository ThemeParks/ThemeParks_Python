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

from collections.abc import AsyncIterator, Callable, Iterator
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


class HistoryPage(NamedTuple):
    """One page of daily history, as the server described it.

    `start` and `end` are the park-local days the page ACTUALLY covered, which is
    not the range you asked for: a park daily call serves at most 31 days, so a
    50-day request comes back as 31 days plus a `next`. `next_url` is the URL of
    the following page, or None on the last one.

    This exists for resumable downloads. A checkpoint taken from the ROWS is
    wrong in both directions: the newest row's date can be earlier than the page
    covered, because an entity that stopped reporting has no rows for the tail
    days, so resuming there re-fetches days already written and duplicates them;
    and a half-written page is indistinguishable from a finished one. The page
    boundary is the server's own answer to "where do I carry on", so it is the
    only safe checkpoint.
    """

    start: str
    end: str
    next_url: str | None


class EntityRef(NamedTuple):
    """Who a history row belongs to, AS THE HISTORY RESPONSE REPORTS IT.

    The name matters and the source of it matters more. A park's current
    `/children` list gives today's name, which is the wrong label for a row
    recorded years ago: rides are renamed, and stamping today's name on old data
    quietly rewrites history. The history envelope carries its own `name` and
    `entityType` per entity, and that is the name to use.
    """

    id: str
    name: str
    entity_type: str


def _ref(entity: Any) -> EntityRef:
    kind = getattr(entity, "entityType", None)
    # The generated models use an enum, and str(EntityType.SHOW) is
    # "EntityType.SHOW". `.value` is what the API sends.
    inner = getattr(kind, "value", kind)
    return EntityRef(
        entity.id, getattr(entity, "name", "") or "", "" if inner is None else str(inner)
    )


def _page_of(envelope: DailyEnvelope) -> HistoryPage:
    """The page an envelope represents, for :class:`HistoryPage`'s callers."""
    rng = getattr(envelope, "range", None)
    nxt = getattr(envelope, "next", None)
    return HistoryPage(
        getattr(rng, "from_", "") or "",
        getattr(rng, "to", "") or "",
        nxt or None,
    )


def _daily_entity_rows(envelope: DailyEnvelope) -> Iterator[tuple[EntityRef, HistoryDailyRow]]:
    """Yield (entity ref, row), keeping the name the response gave.

    `_daily_rows` below is the same walk with the ref flattened to its id, kept
    because `days()` has yielded `(id, row)` since 3.0 and that shape is public.
    """
    entities = getattr(envelope, "entities", None)
    if entities is not None:
        for entity in entities:
            ref = _ref(entity)
            for row in entity.days or []:
                yield (ref, row)
        return
    ref = _ref(envelope)
    for row in getattr(envelope, "days", None) or []:
        yield (ref, row)


def _daily_rows(envelope: DailyEnvelope) -> Iterator[tuple[str, HistoryDailyRow]]:
    """Yield (entity id, row). A park envelope carries many entities; an entity
    envelope carries its own rows, so both flatten to the same stream."""
    for ref, row in _daily_entity_rows(envelope):
        yield (ref.id, row)


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

    def days_with_entities(
        self,
        start: str | _date | None = None,
        end: str | _date | None = None,
        *,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
        on_page: Callable[[HistoryPage], None] | None = None,
    ) -> Iterator[tuple[EntityRef, HistoryDailyRow]]:
        """`days()`, but each row arrives with the entity's name and type.

        Use this when you are writing history to a file. The name comes from the
        history response itself, so it is the label that response gives for those
        rows rather than the park's current `/children` list -- rides get renamed,
        and today's name on a row from three years ago is a quiet rewrite of the
        record.

        It also saves a request: the name is already in the payload, so nothing
        needs to ask what an id refers to.

        `on_page` is called once every row of a page has been yielded, with a
        :class:`HistoryPage`. Checkpoint on that, never on the last row you saw.
        """
        envelope: DailyEnvelope | None = self._first_daily(start, end, max_wait)
        while envelope is not None:
            yield from _daily_entity_rows(envelope)
            # AFTER the rows, never before: a caller checkpointing on this has to
            # be able to trust that everything the page held is already written.
            if on_page is not None:
                on_page(_page_of(envelope))
            envelope = self._next_daily(envelope, max_wait)

    def days(
        self,
        start: str | _date | None = None,
        end: str | _date | None = None,
        *,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
        on_page: Callable[[HistoryPage], None] | None = None,
    ) -> Iterator[tuple[str, HistoryDailyRow]]:
        """One summary row per park-local day, as (entity id, row).

        Pages automatically. Given a park id this uses the park call, which
        answers every entity in the park in one request.

        `days_with_entities()` is the same stream with the entity's name and type
        attached; this shape is kept because it is public API from 3.0.
        """
        envelope: DailyEnvelope | None = self._first_daily(start, end, max_wait)
        while envelope is not None:
            yield from _daily_rows(envelope)
            if on_page is not None:
                on_page(_page_of(envelope))
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
        on_page: Callable[[HistoryPage], None] | None = None,
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
            if on_page is not None:
                on_page(_page_of(envelope))
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
