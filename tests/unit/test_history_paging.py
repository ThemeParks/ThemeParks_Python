"""Paging over daily history, against two real consecutive pages.

`mk_park_daily_page1.json` and `mk_park_daily_page2.json` are one real request
and the `next` it handed back, captured verbatim. They are the oracle for the
one thing a resumable download depends on: where a page ends and where the
server says to carry on. Page one covers through 2026-08-31, two of its three
entities stop reporting on 2026-08-30, and the server says continue at
2026-09-01 -- so the newest row, the page's last day, and the resume point are
three different values, and a test cannot confuse them by accident.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from themeparks._ergonomic.history import HistoryApi, HistoryPage
from themeparks._generated.models import HistoryDailyRow
from themeparks._models_base import ApiModel
from themeparks._raw import _parse_daily_history

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _page(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Raw:
    """The generated client, answering the two captured pages in order."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.paths: list[str] = []

    def get_entity_history_daily(self, entity_id: str, start=None, end=None):
        self.paths.append(f"first:{start}:{end}")
        return _parse_daily_history(self._pages[0])

    def get_path(self, url: str):
        self.paths.append(url)
        return self._pages[len(self.paths) - 1]


def _api() -> tuple[HistoryApi, _Raw]:
    raw = _Raw([_page("mk_park_daily_page1.json"), _page("mk_park_daily_page2.json")])
    return HistoryApi(raw, "75ea578a-adc8-4116-a54d-dccb60765ef9"), raw


class TestThePageHook:
    def test_reports_the_range_and_next_the_server_gave(self) -> None:
        api, _ = _api()
        pages: list[HistoryPage] = []
        list(api.days_with_entities("2026-08-01", "2026-09-20", on_page=pages.append))
        assert pages == [
            HistoryPage("2026-08-01", "2026-08-31", _page("mk_park_daily_page1.json")["next"]),
            HistoryPage("2026-09-01", "2026-09-20", None),
        ]

    def test_fires_only_after_every_row_of_its_page(self) -> None:
        # A resumable download checkpoints on this. Firing first would record a
        # checkpoint past rows that were never written, and those rows would be
        # missing from the file for good -- the one failure worse than
        # duplicating them.
        api, _ = _api()
        order: list[str] = []
        for _ref, row in api.days_with_entities(
            "2026-08-01", "2026-09-20", on_page=lambda p: order.append(f"page:{p.end}")
        ):
            order.append(f"row:{row.date.isoformat()}")
        boundary = order.index("page:2026-08-31")
        page1 = _page("mk_park_daily_page1.json")
        page1_rows = sum(len(e["days"]) for e in page1["entities"])
        # COUNTED, not indexed. `order[boundary - 1]` was the first assertion
        # here, and with the hook firing first the boundary lands at index 0 and
        # `order[-1]` is the last row of the run, so it passed on the broken
        # ordering -- the assertion tested Python's negative indexing, not the
        # code.
        assert boundary > 0, "the boundary fired before any row"
        before = [item for item in order[:boundary] if item.startswith("row:")]
        assert len(before) == page1_rows == 64
        page1_dates = {day["date"] for e in page1["entities"] for day in e["days"]}
        assert all(item[4:] in page1_dates for item in before)
        assert boundary < len(order) - 1, "the second page never arrived"

    def test_the_newest_row_is_not_the_page_boundary(self) -> None:
        # The defect this whole mechanism exists for, stated as data: if these
        # were equal, resuming from the newest row would be harmless and none of
        # this would be needed.
        page1 = _page("mk_park_daily_page1.json")
        newest = max(day["date"] for e in page1["entities"] for day in e["days"])
        assert page1["range"]["to"] == newest
        stops_early = [
            e["name"] for e in page1["entities"] if e["days"][-1]["date"] < page1["range"]["to"]
        ]
        assert len(stops_early) == 2, stops_early
        assert "from=2026-09-01" in page1["next"]


class TestRowsKeepEverythingTheApiSent:
    def test_the_undocumented_fields_survive_parsing(self) -> None:
        # Pydantic drops what the model does not declare, so a stale vendored
        # schema silently deletes data on the way in -- not just from the CSV,
        # from every Python caller. `unknownMinutes`, `inParkHours` and
        # `extremeWaits` were on every row the API returned and in none of the
        # models, so they never reached anyone.
        api, _ = _api()
        rows = [row for _ref, row in api.days_with_entities("2026-08-01", "2026-09-20")]
        assert len(rows) == 64 + 44
        assert any(r.unknownMinutes is not None for r in rows)
        with_hours = [r for r in rows if r.inParkHours is not None]
        assert with_hours, "inParkHours never survived the parse"
        assert with_hours[0].inParkHours.scheduledMinutes is not None

    def test_each_row_is_labelled_from_the_response(self) -> None:
        api, _ = _api()
        page1 = _page("mk_park_daily_page1.json")
        names = {e["id"]: (e["name"], e["entityType"]) for e in page1["entities"]}
        seen = set()
        for ref, _row in api.days_with_entities("2026-08-01", "2026-09-20"):
            assert (ref.name, ref.entity_type) == names[ref.id]
            seen.add(ref.entity_type)
        assert len(seen) == 3, seen


class TestAFieldTheSchemaDoesNotKnowSurvives:
    """The SDK must not delete data because its vendored spec is a week behind.

    Pydantic's default is to drop undeclared fields. The spec trailed the API by
    days, and in that window `unknownMinutes`, `inParkHours` and `extremeWaits`
    were deleted at parse time for every caller of `days()` -- not untyped, gone,
    with nothing failing and nothing warning. The models inherit
    `themeparks._models_base.ApiModel` now, whose only job is `extra="allow"`.
    """

    def test_an_unknown_field_reaches_the_caller(self) -> None:
        page = _page("mk_park_daily_page1.json")
        page["next"] = None  # one page: this is about parsing, not paging
        # A field this SDK has never heard of, in the shape a new API field arrives
        # in: present on the row, absent from the schema.
        for entity in page["entities"]:
            for day in entity["days"]:
                day["weatherClosureMinutes"] = 41

        raw = _Raw([page])
        api = HistoryApi(raw, "park")
        rows = [row for _ref, row in api.days_with_entities("2026-08-01", "2026-08-31")]
        assert rows, "no rows parsed"
        # Reachable as an attribute, and in the dump the NDJSON writer uses.
        assert rows[0].weatherClosureMinutes == 41
        assert rows[0].model_dump(mode="json")["weatherClosureMinutes"] == 41

    def test_the_base_class_is_the_reason(self) -> None:
        # Pin the mechanism, not just the symptom: a regeneration that loses the
        # --base-class flag would put the silent deletion straight back.
        assert issubclass(HistoryDailyRow, ApiModel)
        assert HistoryDailyRow.model_config.get("extra") == "allow"
