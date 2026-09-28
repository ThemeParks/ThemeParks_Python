"""`changes()` hands back the `opening` state, so a day can be rebuilt from it.

Every row of `/history` is the complete live data from its `time` until the next
row. What the rows cannot say is the state BEFORE the first of them: that is the
envelope's `opening` object, and `changes()` used to yield the rows and throw the
envelope away. A caller rebuilding a day then had no status for the minutes
between the start of the range and the first change, which on a night a ride runs
past midnight is real operating time.

The oracle here is a real capture (see tests/fixtures/README.md): Space Mountain
on 2026-09-26, whose opening is OPERATING because the previous night's extra hours
ran past midnight, and the daily summary the API computed for the same day.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from themeparks import AsyncThemeParks, ThemeParks
from themeparks._ergonomic.history import HistoryChanges
from themeparks._generated.models import HistoryOpening

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RAW = json.loads((FIXTURES / "space_mountain_history_2026-09-26.json").read_text(encoding="utf-8"))
DAILY = json.loads((FIXTURES / "space_mountain_daily_2026-09-26.json").read_text(encoding="utf-8"))
SPACE_MOUNTAIN = RAW["id"]


def _client(payload: dict, seen: list[str] | None = None) -> ThemeParks:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(200, json=payload)

    return ThemeParks(transport=httpx.MockTransport(handler), cache=False)


def _async_client(payload: dict) -> AsyncThemeParks:
    return AsyncThemeParks(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        cache=False,
    )


def _timeline(
    opening: HistoryOpening | None, rows: list, day_start: datetime, day_end: datetime
) -> list[tuple[datetime, datetime, str | None]]:
    """(from, to, status) segments covering the day, the way a customer rebuilds one.

    Without an opening, the stretch before the first change has no known status,
    which is exactly the gap this change closes.
    """
    segments = []
    cursor = day_start
    status = opening.status if opening is not None else None
    for row in rows:
        if row.time > cursor:
            segments.append((cursor, row.time, status))
        cursor = max(cursor, row.time)
        status = row.status
    segments.append((cursor, day_end, status))
    return segments


def _seconds(segments, wanted: str | None) -> float:
    return sum((b - a).total_seconds() for a, b, s in segments if s == wanted)


class TestTheCaptureIsWhatThisFileSays:
    def test_the_opening_carries_a_run_over_midnight(self) -> None:
        # Guard the oracle. If a re-capture picks a day whose opening is CLOSED,
        # every test below still passes with and without the fix.
        assert RAW["opening"]["status"] == "OPERATING"
        assert RAW["history"][0]["status"] == "CLOSED"
        assert RAW["history"][0]["time"] > RAW["opening"]["time"]


class TestOpeningIsExposed:
    def test_the_rows_are_unchanged(self) -> None:
        # Backwards compatible: iterating yields exactly what 4.0 yielded.
        rows = list(_client(RAW).entity(SPACE_MOUNTAIN).history.changes("2026-09-26"))
        assert len(rows) == len(RAW["history"])
        assert all(entity_id == SPACE_MOUNTAIN for entity_id, _ in rows)
        assert rows[0][1].status == "CLOSED"

    def test_opening_is_keyed_by_entity_id(self) -> None:
        changes = _client(RAW).entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
        opening = changes.opening[SPACE_MOUNTAIN]
        assert isinstance(opening, HistoryOpening)
        assert opening.status == "OPERATING"
        assert opening.time == datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)
        assert opening.queue is not None and opening.queue.STANDBY is not None
        assert opening.queue.STANDBY.waitTime == 15

    def test_opening_before_iterating_costs_one_request_not_two(self) -> None:
        seen: list[str] = []
        changes = _client(RAW, seen).entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
        assert seen == [], "the request is made when the result is first used, as before"
        assert changes.opening[SPACE_MOUNTAIN].status == "OPERATING"
        rows = list(changes)
        assert len(rows) == len(RAW["history"])
        assert len(seen) == 1

    def test_it_is_still_an_iterator(self) -> None:
        changes = _client(RAW).entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
        assert isinstance(changes, HistoryChanges)
        assert iter(changes) is changes
        first = next(changes)
        assert first[1].status == "CLOSED"

    def test_a_park_gives_one_opening_per_entity(self) -> None:
        park = {
            "id": "park-1",
            "name": "Magic Kingdom Park",
            "entityType": "PARK",
            "timezone": "America/New_York",
            "range": {"from": "2026-09-26", "to": "2026-09-26"},
            "entities": [
                {
                    "id": SPACE_MOUNTAIN,
                    "name": "Space Mountain",
                    "entityType": "ATTRACTION",
                    "coverage": RAW["coverage"],
                    "opening": RAW["opening"],
                    "history": RAW["history"][:2],
                },
                {
                    "id": "quiet-ride",
                    "name": "Nothing Changed Today",
                    "entityType": "ATTRACTION",
                    "coverage": {"firstRecordedAt": "2021-07-03"},
                    "opening": {"time": "2026-09-26T04:00:00Z", "status": "REFURBISHMENT"},
                    "history": [],
                },
            ],
            "next": None,
        }
        changes = _client(park).entity("park-1").history.changes("2026-09-26")
        assert list(changes.opening) == [SPACE_MOUNTAIN, "quiet-ride"]
        # An entity with no rows at all is only knowable through its opening.
        assert changes.opening["quiet-ride"].status == "REFURBISHMENT"
        assert [entity_id for entity_id, _ in changes] == [SPACE_MOUNTAIN, SPACE_MOUNTAIN]

    def test_an_error_still_surfaces_on_first_use(self) -> None:
        tp = _client(RAW)
        changes = tp.entity(SPACE_MOUNTAIN).history.changes("2026-09-26", start="2026-09-26")
        with pytest.raises(ValueError, match="either date, or from/to"):
            _ = changes.opening


class TestADayRebuildsFromOpeningPlusChanges:
    """What the opening is FOR, checked against the API's own daily row."""

    DAY_START = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)  # 00:00 EDT
    DAY_END = DAY_START + timedelta(days=1)

    def _rebuild(self, *, with_opening: bool):
        changes = _client(RAW).entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
        rows = [row for _, row in changes]
        opening = changes.opening[SPACE_MOUNTAIN] if with_opening else None
        return _timeline(opening, rows, self.DAY_START, self.DAY_END)

    def test_every_second_of_the_day_has_a_known_status(self) -> None:
        segments = self._rebuild(with_opening=True)
        assert sum((b - a).total_seconds() for a, b, _ in segments) == 86400
        assert _seconds(segments, None) == 0

    def test_without_the_opening_the_first_minute_is_unknown(self) -> None:
        # The defect, measured: 63 seconds of a ride OPERATING past midnight that
        # a rebuild from rows alone cannot place.
        assert _seconds(self._rebuild(with_opening=False), None) == 63

    def test_the_rebuild_agrees_with_the_daily_row(self) -> None:
        # An oracle this repo did not write: the API's summary of the same day.
        # The first change TO operating and the last close are the daily row's
        # firstOperatingAt and lastClosedAt.
        segments = self._rebuild(with_opening=True)
        daily = DAILY["days"][0]
        opened = [a for a, _b, s in segments if s == "OPERATING" and a > self.DAY_START]
        closed = [b for _a, b, s in segments if s == "OPERATING" and b < self.DAY_END]
        assert opened[0].isoformat().replace("+00:00", "Z") == daily["firstOperatingAt"]
        assert closed[-1].isoformat().replace("+00:00", "Z") == daily["lastClosedAt"]
        # 63 s carried over midnight, then 11:30:53Z to 03:01:04Z.
        assert _seconds(segments, "OPERATING") == 63 + 55811


class TestAsyncOpening:
    async def test_opening_after_iterating(self) -> None:
        async with _async_client(RAW) as tp:
            changes = tp.entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
            rows = [pair async for pair in changes]
            assert len(rows) == len(RAW["history"])
            assert changes.opening[SPACE_MOUNTAIN].status == "OPERATING"

    async def test_load_fetches_it_up_front(self) -> None:
        async with _async_client(RAW) as tp:
            changes = await tp.entity(SPACE_MOUNTAIN).history.changes("2026-09-26").load()
            assert changes.opening[SPACE_MOUNTAIN].status == "OPERATING"
            assert len([pair async for pair in changes]) == len(RAW["history"])

    async def test_opening_before_the_response_says_how_to_get_it(self) -> None:
        async with _async_client(RAW) as tp:
            changes = tp.entity(SPACE_MOUNTAIN).history.changes("2026-09-26")
            with pytest.raises(RuntimeError, match="load"):
                _ = changes.opening
