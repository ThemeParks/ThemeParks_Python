"""Entity navigation helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import date

from themeparks._generated.models import (
    EntityChild,
    EntityChildrenResponse,
    EntityData,
    EntityLiveDataResponse,
    EntityScheduleResponse,
    ScheduleEntry,
)
from themeparks._raw import AsyncRawClient, RawClient

_MONTHS_PER_YEAR = 12


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > _MONTHS_PER_YEAR:
            m = 1
            y += 1
    return months


class _ScheduleApi:
    def __init__(self, raw: RawClient, entity_id: str) -> None:
        self._raw = raw
        self._id = entity_id

    def upcoming(self) -> EntityScheduleResponse:
        return self._raw.get_entity_schedule(self._id)

    def month(self, year: int, month: int) -> EntityScheduleResponse:
        return self._raw.get_entity_schedule_month(self._id, year, month)

    def range(self, start: date, end: date) -> list[ScheduleEntry]:
        if end < start:
            return []
        results: list[ScheduleEntry] = []
        for y, m in _months_between(start, end):
            resp = self._raw.get_entity_schedule_month(self._id, y, m)
            results.extend(resp.schedule or [])
        return sorted(
            [e for e in results if start.isoformat() <= (e.date or "") <= end.isoformat()],
            key=lambda e: e.date or "",
        )


class EntityHandle:
    """Fluent, read-only navigation rooted at one entity id.

    Obtained via ``tp.entity(entity_id)``. Provides :meth:`get` for the entity
    record, :meth:`children` for direct children, :meth:`live` for current
    wait/queue data, :meth:`walk` for an iterator over every descendant (one
    API call), and ``.schedule`` for ``upcoming`` / ``month`` / ``range``
    helpers. All calls flow through the shared
    :class:`~themeparks._raw.RawClient` and therefore share the client's cache.

    Example:
        >>> with ThemeParks() as tp:
        ...     mk = tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9")
        ...     live = mk.live()

    """

    def __init__(self, *, raw: RawClient, entity_id: str) -> None:
        self._raw = raw
        self.entity_id = entity_id
        self.schedule = _ScheduleApi(raw, entity_id)

    def get(self) -> EntityData:
        return self._raw.get_entity(self.entity_id)

    def children(self) -> EntityChildrenResponse:
        return self._raw.get_entity_children(self.entity_id)

    def live(self) -> EntityLiveDataResponse:
        return self._raw.get_entity_live(self.entity_id)

    def walk(self) -> Iterator[EntityChild]:
        """Yield every descendant of this entity in a single API call.

        The ThemeParks API's ``/children`` endpoint returns the entire
        subtree in one response — this method just exposes it as an
        iterator. The root entity itself is not yielded.
        """
        res = self._raw.get_entity_children(self.entity_id)
        yield from res.children or []


class _AsyncScheduleApi:
    def __init__(self, raw: AsyncRawClient, entity_id: str) -> None:
        self._raw = raw
        self._id = entity_id

    async def upcoming(self) -> EntityScheduleResponse:
        return await self._raw.get_entity_schedule(self._id)

    async def month(self, year: int, month: int) -> EntityScheduleResponse:
        return await self._raw.get_entity_schedule_month(self._id, year, month)

    async def range(self, start: date, end: date) -> list[ScheduleEntry]:
        if end < start:
            return []
        responses = await asyncio.gather(
            *(
                self._raw.get_entity_schedule_month(self._id, y, m)
                for y, m in _months_between(start, end)
            )
        )
        flat = [e for r in responses for e in (r.schedule or [])]
        return sorted(
            [e for e in flat if start.isoformat() <= (e.date or "") <= end.isoformat()],
            key=lambda e: e.date or "",
        )


class AsyncEntityHandle:
    """Asynchronous mirror of :class:`EntityHandle`.

    Same method set (``get``, ``children``, ``live``, ``walk``, ``schedule``)
    but all network-bound methods are coroutines. :meth:`walk` exposes the
    recursive ``/children`` response as an async iterator, and
    ``schedule.range`` fans out month fetches via ``asyncio.gather``.

    Example:
        >>> async with AsyncThemeParks() as tp:
        ...     async for child in tp.entity(root_id).walk():
        ...         ...

    """

    def __init__(self, *, raw: AsyncRawClient, entity_id: str) -> None:
        self._raw = raw
        self.entity_id = entity_id
        self.schedule = _AsyncScheduleApi(raw, entity_id)

    async def get(self) -> EntityData:
        return await self._raw.get_entity(self.entity_id)

    async def children(self) -> EntityChildrenResponse:
        return await self._raw.get_entity_children(self.entity_id)

    async def live(self) -> EntityLiveDataResponse:
        return await self._raw.get_entity_live(self.entity_id)

    async def walk(self) -> AsyncIterator[EntityChild]:
        """Yield every descendant of this entity in a single API call.

        The ThemeParks API's ``/children`` endpoint returns the entire
        subtree in one response — this method just exposes it as an
        async iterator. The root entity itself is not yielded.
        """
        res = await self._raw.get_entity_children(self.entity_id)
        for child in res.children or []:
            yield child
