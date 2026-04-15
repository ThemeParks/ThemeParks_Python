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
        visited = {self.entity_id}
        queue = [self.entity_id]
        while queue:
            current = queue.pop(0)
            res = self._raw.get_entity_children(current)
            for child in res.children or []:
                if child.id in visited:
                    continue
                visited.add(child.id)
                queue.append(child.id)
                yield child


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

    async def walk(self, *, concurrency: int = 8) -> AsyncIterator[EntityChild]:
        """BFS over an entity's descendants with bounded parallelism.

        Fetches all children within a BFS level concurrently (capped by
        ``concurrency``) before advancing to the next level. Yields children
        in BFS order and deduplicates by id.
        """
        visited = {self.entity_id}
        current_level = [self.entity_id]
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_children(entity_id: str) -> EntityChildrenResponse:
            async with semaphore:
                return await self._raw.get_entity_children(entity_id)

        while current_level:
            responses = await asyncio.gather(*(fetch_children(eid) for eid in current_level))
            next_level: list[str] = []
            for res in responses:
                for child in res.children or []:
                    if child.id in visited:
                        continue
                    visited.add(child.id)
                    next_level.append(child.id)
                    yield child
            current_level = next_level
