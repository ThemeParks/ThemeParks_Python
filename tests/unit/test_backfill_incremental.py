"""`themeparks-backfill` over time: a date range, reruns that add new days, and
only final days in the file.

Three defects from one customer-style run of 4.0.1, each reproduced before it
was fixed:

1. No way to ask for less than everything. A key that reaches the whole archive
   downloaded all of it, every time, with no `--since`.
2. A finished park was finished forever. A rerun printed "already complete" and
   exited 0 without asking for a single new day, so a nightly cron looked
   healthy and never updated. The only way to get yesterday was `--overwrite`,
   which downloads the whole archive again.
3. The run ended at `retrievableThrough`, which is today. Today's row is the day
   so far, and the archive records days 2 to 3 behind, so the newest rows in the
   file were partial -- Magic Kingdom's last day summed to about half the
   operating minutes of a full one -- and, because of (2), they were never
   corrected.

The stub below serves a park the way the API does: one row per entity per day,
31 days a page, final values through `recorded_to` and partial values after it.
A partial row carries half the operating minutes, so a test can tell from the
file alone whether a non-final day was ever written.
"""

from __future__ import annotations

import csv
import inspect
import io
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from themeparks import APIError, BudgetExhaustedError, backfill
from themeparks._client import PACKAGE_VERSION
from themeparks._ergonomic.history import EntityRef, HistoryApi, HistoryPage, HistorySpan
from themeparks._generated.models import HistoryDailyRow
from themeparks.backfill import _Park, _Range

FINAL_MINUTES = 600
PARTIAL_MINUTES = 300
PAGE_DAYS = 31


def _day(value: object) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _row(day: date, *, final: bool) -> HistoryDailyRow:
    return HistoryDailyRow.model_validate(
        {
            "date": day.isoformat(),
            "firstOperatingAt": f"{day.isoformat()}T13:00:00Z",
            "lastClosedAt": f"{day.isoformat()}T23:00:00Z" if final else None,
            "operatingMinutes": FINAL_MINUTES if final else PARTIAL_MINUTES,
            "downMinutes": 0,
            "changes": 3,
        }
    )


class _Archive:
    """A park's daily history, served as the API serves it.

    `through` is retrievableThrough (usually today), `recorded_to` the newest day
    the archive holds. Days after `recorded_to` are served with partial values,
    which is what today's row and the days still being recorded look like.
    """

    def __init__(
        self,
        archive_from: str = "2026-06-01",
        recorded_to: str = "2026-09-26",
        through: str = "2026-09-28",
        floor: str | None = None,
        entities: tuple[str, ...] = ("ent-a", "ent-b"),
    ) -> None:
        self.archive_from = archive_from
        self.recorded_to = recorded_to
        self.through = through
        self.floor = floor
        self.entities = entities
        self.calls: list[tuple[str, str]] = []
        #: Raise BudgetExhaustedError on the Nth page request (1-based), or never.
        self.budget_on_page: int | None = None
        self._pages_served = 0

    def advance(self, days: int) -> None:
        """Time passes: the archive and today both move on."""
        self.recorded_to = (_day(self.recorded_to) + timedelta(days=days)).isoformat()
        self.through = (_day(self.through) + timedelta(days=days)).isoformat()

    def span(self) -> HistorySpan:
        return HistorySpan(_day(self.archive_from), _day(self.recorded_to), _day(self.through))

    def days_with_entities(self, start=None, end=None, *, max_wait=120.0, on_page=None):
        self.calls.append((str(start), str(end)))
        if self.floor is not None and str(start) < self.floor:
            raise backfill_test_403(self.floor)
        first = _day(start)
        last = min(_day(end), _day(self.through))
        page_start = first
        while page_start <= last:
            self._pages_served += 1
            if self.budget_on_page == self._pages_served:
                raise BudgetExhaustedError("429", status=429, body={}, url="u", retry_after=2700.0)
            page_end = min(page_start + timedelta(days=PAGE_DAYS - 1), last)
            for entity in self.entities:
                ref = EntityRef(entity, f"Ride {entity}", "ATTRACTION")
                day = page_start
                while day <= page_end:
                    yield ref, _row(day, final=day <= _day(self.recorded_to))
                    day += timedelta(days=1)
            following = page_end + timedelta(days=1)
            has_next = following <= last
            if on_page is not None:
                nxt = (
                    f"https://api.themeparks.wiki/v1/entity/p/history/daily"
                    f"?from={following.isoformat()}&to={last.isoformat()}"
                    if has_next
                    else None
                )
                on_page(HistoryPage(page_start.isoformat(), page_end.isoformat(), nxt))
            page_start = following


def backfill_test_403(earliest: str) -> APIError:
    return APIError(
        f"403 Forbidden: This key can see history back to {earliest}.",
        status=403,
        body={
            "error": {
                "type": "HISTORY_WINDOW_EXCEEDED",
                "message": f"This key can see history back to {earliest} (400 days).",
                "earliestAllowedDate": earliest,
            }
        },
        url="https://api.themeparks.wiki/v1/entity/p/history/daily",
    )


class _Client:
    def __init__(self, archive: _Archive) -> None:
        self._archive = archive

    def entity(self, _id: str):
        archive = self._archive

        class _Entity:
            history = archive

        return _Entity()


def _run(archive: _Archive, tmp_path: Path, fmt: str = "ndjson", **kw) -> int:
    return backfill.backfill_park(_Client(archive), _Park("p", "Park"), tmp_path, fmt, **kw)


def _rows(tmp_path: Path, fmt: str = "ndjson") -> list[dict]:
    path = tmp_path / f"p.{fmt}"
    if fmt == "csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _state(tmp_path: Path, fmt: str = "ndjson") -> dict:
    return json.loads(backfill.state_path_for(tmp_path, "p", fmt).read_text(encoding="utf-8"))


def _keys(rows: list[dict]) -> Counter:
    return Counter((r["entityId"], r["date"]) for r in rows)


def _assert_every_row_final_and_unique(rows: list[dict]) -> None:
    keys = _keys(rows)
    dupes = [k for k, n in keys.items() if n > 1]
    assert dupes == [], f"(entityId, date) written more than once: {dupes[:5]}"
    partial = [r["date"] for r in rows if int(r["operatingMinutes"]) != FINAL_MINUTES]
    assert partial == [], f"non-final days reached the file: {sorted(set(partial))}"


class TestTheStubIsTheApi:
    def test_the_stub_takes_what_the_real_method_takes(self) -> None:
        real = inspect.signature(HistoryApi.days_with_entities)
        stub = inspect.signature(_Archive.days_with_entities)
        assert list(real.parameters) == list(stub.parameters)

    def test_the_stub_serves_partial_days_past_recorded_to(self) -> None:
        # Guard the oracle: if the stub never served a partial row, "no partial
        # row in the file" would pass whether or not the command stops early.
        archive = _Archive()
        served = list(archive.days_with_entities("2026-09-25", "2026-09-28"))
        minutes = {row.date.isoformat(): row.operatingMinutes for _, row in served}
        assert minutes["2026-09-26"] == FINAL_MINUTES
        assert minutes["2026-09-27"] == PARTIAL_MINUTES
        assert minutes["2026-09-28"] == PARTIAL_MINUTES


class TestOnlyFinalDaysAreWritten:
    """Defect 3: the newest rows were partial, and then frozen."""

    def test_the_run_stops_at_the_newest_final_day(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        assert _run(archive, tmp_path) == 0
        assert archive.calls == [("2026-06-01", "2026-09-26")]
        rows = _rows(tmp_path)
        assert max(r["date"] for r in rows) == "2026-09-26"
        _assert_every_row_final_and_unique(rows)
        err = capsys.readouterr().err
        assert "stopping at 2026-09-26" in err
        assert _state(tmp_path)["end"] == "2026-09-26"

    def test_the_next_run_adds_those_days_once_they_are_final(self, tmp_path: Path) -> None:
        # The other half: the days held back are not lost, they arrive on the
        # next run with their final values, once.
        archive = _Archive()
        _run(archive, tmp_path)
        archive.advance(2)
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1] == ("2026-09-27", "2026-09-28")
        rows = _rows(tmp_path)
        assert max(r["date"] for r in rows) == "2026-09-28"
        _assert_every_row_final_and_unique(rows)

    def test_nothing_final_yet_writes_nothing(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()

        def span() -> HistorySpan:
            return HistorySpan(None, None, date(2026, 9, 28))

        archive.span = span  # type: ignore[method-assign]
        assert _run(archive, tmp_path) == 0
        assert archive.calls == []
        assert not (tmp_path / "p.ndjson").exists()
        assert "nothing final" in capsys.readouterr().err

    def test_no_message_when_the_archive_is_already_final(self, tmp_path: Path, capsys) -> None:
        # A park that stopped reporting: everything it has is final, and saying
        # "stopping early" would be noise.
        archive = _Archive(recorded_to="2026-08-31", through="2026-08-31")
        _run(archive, tmp_path)
        assert "stopping at" not in capsys.readouterr().err


class TestARerunAddsNewDays:
    """Defect 2: a finished park never fetched another day."""

    def test_a_rerun_with_new_final_days_fetches_only_those(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path)
        before = _rows(tmp_path)
        archive.advance(1)
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1] == ("2026-09-27", "2026-09-27")
        after = _rows(tmp_path)
        assert after[: len(before)] == before, "the rows already there changed"
        assert len(after) == len(before) + 2  # two entities, one new day
        _assert_every_row_final_and_unique(after)

    def test_the_state_keeps_the_original_start_and_moves_the_end(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path)
        archive.advance(3)
        _run(archive, tmp_path)
        state = _state(tmp_path)
        assert state["start"] == "2026-06-01"
        assert state["end"] == "2026-09-29"
        assert state["complete"] is True

    def test_a_rerun_with_nothing_new_asks_for_nothing(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        _run(archive, tmp_path)
        capsys.readouterr()
        before = (tmp_path / "p.ndjson").read_bytes()
        assert _run(archive, tmp_path) == 0
        assert len(archive.calls) == 1, "asked the API again with nothing to ask for"
        assert (tmp_path / "p.ndjson").read_bytes() == before
        assert "up to date" in capsys.readouterr().err

    def test_a_nightly_cron_for_a_week(self, tmp_path: Path) -> None:
        # The use case exit code 75 is designed for, end to end.
        archive = _Archive()
        for _ in range(7):
            assert _run(archive, tmp_path) == 0
            archive.advance(1)
        rows = _rows(tmp_path)
        assert max(r["date"] for r in rows) == "2026-10-02"
        _assert_every_row_final_and_unique(rows)
        days = sorted({r["date"] for r in rows})
        expected = (date(2026, 10, 2) - date(2026, 6, 1)).days + 1
        assert len(days) == expected, "a gap or an overlap between runs"

    def test_a_csv_rerun_gains_no_second_header_or_bom(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path, "csv")
        archive.advance(2)
        _run(archive, tmp_path, "csv")
        raw = (tmp_path / "p.csv").read_bytes()
        assert raw.count(b"\xef\xbb\xbf") == 1
        assert raw.decode("utf-8-sig").count("parkId,") == 1
        _assert_every_row_final_and_unique(_rows(tmp_path, "csv"))

    def test_an_interrupted_rerun_continues_from_its_own_start(self, tmp_path: Path) -> None:
        # The budget runs out on the rerun's FIRST request, before any page. With
        # no page boundary and no row, the only thing saying where this run began
        # is the state file; without it the next run started over from the top
        # of the archive and appended a second copy of everything.
        archive = _Archive()
        _run(archive, tmp_path)
        rows_before = len(_rows(tmp_path))
        archive.advance(40)
        archive.budget_on_page = archive._pages_served + 1
        assert _run(archive, tmp_path) == backfill.EX_TEMPFAIL
        assert len(_rows(tmp_path)) == rows_before, "the file changed on a failed run"
        assert _state(tmp_path)["resumeFrom"] == "2026-09-27"

        archive.budget_on_page = None
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1][0] == "2026-09-27"
        _assert_every_row_final_and_unique(_rows(tmp_path))

    def test_an_interrupted_rerun_mid_way_resumes_at_the_page_boundary(
        self, tmp_path: Path
    ) -> None:
        archive = _Archive()
        _run(archive, tmp_path)
        archive.advance(40)  # two pages of new days
        archive.budget_on_page = archive._pages_served + 2
        assert _run(archive, tmp_path) == backfill.EX_TEMPFAIL
        assert _state(tmp_path)["resumeFrom"] == "2026-10-28"
        archive.budget_on_page = None
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1][0] == "2026-10-28"
        _assert_every_row_final_and_unique(_rows(tmp_path))


class TestSinceAndUntil:
    """Defect 1: no way to ask for less than everything."""

    def test_since_is_where_the_first_request_starts(self, tmp_path: Path) -> None:
        archive = _Archive()
        assert _run(archive, tmp_path, window=_Range(since="2026-09-01")) == 0
        assert archive.calls == [("2026-09-01", "2026-09-26")]
        assert min(r["date"] for r in _rows(tmp_path)) == "2026-09-01"
        assert _state(tmp_path)["start"] == "2026-09-01"

    def test_until_is_where_it_ends(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2026-07-01", until="2026-07-31"))
        assert archive.calls == [("2026-07-01", "2026-07-31")]
        # --until is a choice, not a day held back for being unfinished.
        assert "stopping at" not in capsys.readouterr().err

    def test_until_past_the_final_day_still_stops_at_the_final_day(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(until="2026-12-31"))
        assert archive.calls == [("2026-06-01", "2026-09-26")]
        _assert_every_row_final_and_unique(_rows(tmp_path))

    def test_since_before_the_archive_starts_at_the_archive(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2019-01-01"))
        assert archive.calls[0][0] == "2026-06-01"

    def test_since_before_the_plan_floor_starts_at_the_floor(self, tmp_path: Path, capsys) -> None:
        archive = _Archive(floor="2026-08-01")
        assert _run(archive, tmp_path, window=_Range(since="2026-07-01")) == 0
        assert [c[0] for c in archive.calls] == ["2026-07-01", "2026-08-01"]
        assert "reaches back to 2026-08-01" in capsys.readouterr().err

    def test_since_after_the_newest_final_day_writes_nothing(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        assert _run(archive, tmp_path, window=_Range(since="2026-09-27")) == 0
        assert archive.calls == []
        assert not (tmp_path / "p.ndjson").exists()
        assert "2026-09-26" in capsys.readouterr().err

    def test_the_same_since_on_every_run_continues_the_file(self, tmp_path: Path) -> None:
        # A cron line with a fixed --since, run nightly.
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2026-09-01"))
        archive.advance(1)
        assert _run(archive, tmp_path, window=_Range(since="2026-09-01")) == 0
        assert archive.calls[-1] == ("2026-09-27", "2026-09-27")
        _assert_every_row_final_and_unique(_rows(tmp_path))

    def test_the_same_since_before_the_archive_is_not_a_change(self, tmp_path: Path) -> None:
        # --since 2019 asked for the archive's start; asking again is the same run.
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2019-01-01"))
        archive.advance(1)
        assert _run(archive, tmp_path, window=_Range(since="2019-01-01")) == 0

    def test_a_rolling_since_continues_the_file(self, tmp_path: Path) -> None:
        # `--since $(date -d '-30 days' +%F)` in a cron: later every night, and
        # always inside the file, so the file just carries on.
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2026-08-27"))
        archive.advance(1)
        assert _run(archive, tmp_path, window=_Range(since="2026-08-28")) == 0
        assert archive.calls[-1] == ("2026-09-27", "2026-09-27")

    def test_an_earlier_since_than_the_file_is_refused(self, tmp_path: Path, capsys) -> None:
        # Appending older days after newer ones cannot make the file start
        # earlier without rewriting it, and pretending otherwise would record a
        # range the file does not hold.
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2026-09-01"))
        before = (tmp_path / "p.ndjson").read_bytes()
        assert _run(archive, tmp_path, window=_Range(since="2026-08-01")) == 1
        assert (tmp_path / "p.ndjson").read_bytes() == before
        err = capsys.readouterr().err
        assert "2026-09-01" in err and "--overwrite" in err

    def test_a_since_that_would_leave_a_gap_is_refused(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(until="2026-07-31"))
        assert _run(archive, tmp_path, window=_Range(since="2026-09-01")) == 1
        assert "gap" in capsys.readouterr().err

    def test_an_until_before_the_file_is_refused(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(since="2026-09-01"))
        assert _run(archive, tmp_path, window=_Range(until="2026-08-15")) == 1
        assert "--overwrite" in capsys.readouterr().err

    def test_until_then_no_until_extends_the_file(self, tmp_path: Path) -> None:
        archive = _Archive()
        _run(archive, tmp_path, window=_Range(until="2026-07-31"))
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1] == ("2026-08-01", "2026-09-26")
        _assert_every_row_final_and_unique(_rows(tmp_path))

    def test_an_until_already_covered_is_up_to_date(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        _run(archive, tmp_path)
        assert _run(archive, tmp_path, window=_Range(until="2026-08-01")) == 0
        assert len(archive.calls) == 1
        assert "up to date" in capsys.readouterr().err


class TestTheCommandLine:
    def _main(self, monkeypatch, argv: list[str]) -> list[_Range]:
        seen: list[_Range] = []

        def fake_run_all(tp, targets, args):
            seen.append(_Range(args.since, args.until))
            return 0

        monkeypatch.setattr(backfill, "_catalogue", lambda tp: [("p", "Park", "d", "Dest")])
        monkeypatch.setattr(backfill, "_run_all", fake_run_all)
        monkeypatch.setattr(backfill, "ThemeParks", lambda **kw: _NullClient())
        assert backfill.main([*argv, "--api-key", "tpw_test"]) == 0
        return seen

    def test_since_and_until_reach_the_run(self, tmp_path: Path, monkeypatch) -> None:
        seen = self._main(
            monkeypatch,
            ["p", "--since", "2025-01-01", "--until", "2025-12-31", "--out", str(tmp_path)],
        )
        assert seen == [_Range("2025-01-01", "2025-12-31")]

    def test_neither_is_required(self, tmp_path: Path, monkeypatch) -> None:
        assert self._main(monkeypatch, ["p", "--out", str(tmp_path)]) == [_Range(None, None)]

    @pytest.mark.parametrize(
        "bad", ["2025-13-01", "2025-02-30", "2025-1-1", "20250101", "yesterday", ""]
    )
    def test_a_day_that_is_not_yyyy_mm_dd_is_refused(self, bad: str, capsys) -> None:
        with pytest.raises(SystemExit) as caught:
            backfill.main(["p", "--since", bad])
        assert caught.value.code == 2
        assert "YYYY-MM-DD" in capsys.readouterr().err

    def test_since_after_until_is_refused(self, capsys) -> None:
        with pytest.raises(SystemExit) as caught:
            backfill.main(["p", "--since", "2025-06-01", "--until", "2025-05-31"])
        assert caught.value.code == 2
        assert "--since" in capsys.readouterr().err

    def test_help_documents_both_and_the_final_day_rule(self, capsys) -> None:
        with pytest.raises(SystemExit):
            backfill.main(["--help"])
        out = capsys.readouterr().out
        assert "--since" in out and "--until" in out
        assert "final" in out
        assert "again" in out

    def test_run_all_hands_the_range_to_every_park(self, tmp_path: Path, monkeypatch) -> None:
        got: list[object] = []

        def fake_backfill(tp, park, out_dir, fmt, overwrite=False, **kw):
            got.append(kw["window"])
            return 0

        class Args:
            out = tmp_path
            format = "ndjson"
            overwrite = False
            since = "2025-01-01"
            until = None

        monkeypatch.setattr(backfill, "backfill_park", fake_backfill)
        backfill._run_all(None, [("p1", "One"), ("p2", "Two")], Args())
        assert got == [_Range("2025-01-01", None)] * 2


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# --------------------------------------------------------------------------
# Files written by 4.0.x: their newest days may be partial.
# --------------------------------------------------------------------------


def _v1_state(tmp_path: Path, fmt: str, **fields: object) -> None:
    """A state file with the fields and values 4.0.1 writes for a finished park."""
    state: dict[str, object] = {
        "columns": backfill._columns_fingerprint(fmt),
        "complete": True,
        "end": "2026-09-28",
        "format": fmt,
        "lastDay": "2026-09-28",
        "resumeFrom": None,
        "sdk": "py",
        "sdkVersion": "4.0.1",
        "start": "2026-06-01",
        "stateVersion": 1,
    }
    state.update(fields)
    backfill.state_path_for(tmp_path, "p", fmt).write_text(
        json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_as_4_0(tmp_path: Path, fmt: str, archive: _Archive, names: dict[str, str]) -> None:
    """The file 4.0.1 left behind: every day through `through`, partial tail included."""
    path = tmp_path / f"p.{fmt}"
    ident = backfill._RowIdentity(_Park("p", "Park"))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = backfill.Writer(handle, fmt, True, ident)
        for ref, row in archive.days_with_entities(archive.archive_from, archive.through):
            writer.write(ref._replace(name=names.get(ref.id, ref.name)), row)
    archive.calls.clear()


class TestA40FileIsCorrectedNotFrozen:
    """The partial rows 4.0 wrote are replaced once, on the first run of this build."""

    @pytest.mark.parametrize("fmt", ["ndjson", "csv"])
    def test_the_partial_tail_is_replaced_with_final_rows(self, tmp_path: Path, fmt: str) -> None:
        archive = _Archive()
        _write_as_4_0(tmp_path, fmt, archive, {})
        _v1_state(tmp_path, fmt)
        partial_before = [r for r in _rows(tmp_path, fmt) if int(r["operatingMinutes"]) != 600]
        assert partial_before, "the 4.0 file this test starts from has no partial rows"

        assert _run(archive, tmp_path, fmt) == 0
        # Seven days back from the old end, so every day that could have been
        # partial is fetched again, and nothing earlier is.
        assert archive.calls == [("2026-09-22", "2026-09-26")]
        rows = _rows(tmp_path, fmt)
        assert max(r["date"] for r in rows) == "2026-09-26"
        _assert_every_row_final_and_unique(rows)
        assert _state(tmp_path, fmt)["stateVersion"] == backfill.STATE_VERSION

        archive.advance(2)
        _run(archive, tmp_path, fmt)
        _assert_every_row_final_and_unique(_rows(tmp_path, fmt))

    def test_rows_that_are_kept_are_kept_byte_for_byte(self, tmp_path: Path) -> None:
        # The CSV is parsed and written back, so a name that needs quoting has to
        # come out exactly as it went in. Compared against a file written only
        # up to the cut in the first place.
        nasty = {"ent-a": 'Space, "Mountain"\rFastPass', "ent-b": "=cmd|' /C calc'!A0"}
        archive = _Archive(archive_from="2026-09-01")
        _write_as_4_0(tmp_path, "csv", archive, nasty)
        _v1_state(tmp_path, "csv")
        backfill._trim_after(tmp_path / "p.csv", "csv", "2026-09-21")

        expected_dir = tmp_path / "expected"
        expected_dir.mkdir()
        short = _Archive(archive_from="2026-09-01", recorded_to="2026-09-21", through="2026-09-21")
        _write_as_4_0(expected_dir, "csv", short, nasty)
        assert (tmp_path / "p.csv").read_bytes() == (expected_dir / "p.csv").read_bytes()

    def test_an_ndjson_line_that_does_not_parse_is_kept(self, tmp_path: Path) -> None:
        # Not this command's to judge: a line it cannot read is left where it is.
        path = tmp_path / "p.ndjson"
        path.write_text(
            '{"date": "2026-09-01"}\n{"date": "2026-09-27"}\n{"trunc\n', encoding="utf-8"
        )
        backfill._trim_after(path, "ndjson", "2026-09-21")
        assert path.read_text(encoding="utf-8") == '{"date": "2026-09-01"}\n{"trunc\n'

    def test_a_csv_with_no_date_column_is_copied_whole(self, tmp_path: Path) -> None:
        path = tmp_path / "p.csv"
        path.write_text("\ufeffa,b\n1,2099-01-01\n", encoding="utf-8")
        backfill._trim_after(path, "csv", "2026-09-21")
        assert path.read_text(encoding="utf-8") == "\ufeffa,b\n1,2099-01-01\n"

    def test_an_interrupted_4_0_run_on_its_last_pages_is_trimmed_too(self, tmp_path: Path) -> None:
        # Interrupted close enough to its end to have written unsettled days:
        # those are removed and fetched again, like a finished file's.
        archive = _Archive()
        _write_as_4_0(tmp_path, "ndjson", archive, {})
        _v1_state(tmp_path, "ndjson", complete=False, lastDay="2026-09-28", resumeFrom=None)
        assert _run(archive, tmp_path) == 0
        assert archive.calls == [("2026-09-22", "2026-09-26")]
        _assert_every_row_final_and_unique(_rows(tmp_path))

    def test_an_old_file_whose_newest_day_is_long_final_is_not_rewritten(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A park that stopped reporting: its 4.0 state ends months ago and every
        # row is final. Reading a large file to find nothing to remove is waste.
        archive = _Archive(recorded_to="2026-07-31", through="2026-07-31")
        _write_as_4_0(tmp_path, "ndjson", archive, {})
        _v1_state(tmp_path, "ndjson", end="2026-09-28", lastDay="2026-07-31")
        trimmed: list[str] = []
        monkeypatch.setattr(backfill, "_trim_after", lambda *a: trimmed.append(a[2]))
        assert _run(archive, tmp_path) == 0
        assert trimmed == []

    def test_an_interrupted_4_0_run_resumes_where_it_stopped(self, tmp_path: Path) -> None:
        # Far from the tail, nothing it wrote can be partial: resume as before.
        archive = _Archive()
        path = tmp_path / "p.ndjson"
        path.write_text('{"date": "2026-06-01"}\n', encoding="utf-8")
        _v1_state(tmp_path, "ndjson", complete=False, lastDay="2026-06-30", resumeFrom="2026-07-01")
        assert _run(archive, tmp_path) == 0
        assert archive.calls == [("2026-07-01", "2026-09-26")]

    def test_a_4_0_state_from_the_other_sdk_is_still_refused(self, tmp_path: Path, capsys) -> None:
        archive = _Archive()
        (tmp_path / "p.ndjson").write_text('{"date": "2026-06-01"}\n', encoding="utf-8")
        _v1_state(tmp_path, "ndjson", sdk="js")
        assert _run(archive, tmp_path) == 1
        assert "js SDK" in capsys.readouterr().err


class TestAFinishedFileWrittenToAnotherContractIsRefused:
    def test_a_complete_state_with_another_column_layout_is_not_appended_to(
        self, tmp_path: Path, capsys
    ) -> None:
        # Found while making reruns incremental: only an UNFINISHED mismatched
        # state was refused. A finished one fell through to a fresh start, and
        # the fresh start opened the existing file in append mode -- a second
        # full copy of the archive under a second header, exit 0.
        (tmp_path / "p.csv").write_text("old,header\n1,2\n", encoding="utf-8")
        path = backfill.state_path_for(tmp_path, "p", "csv")
        path.write_text(
            json.dumps(
                {
                    "sdk": "py",
                    "sdkVersion": PACKAGE_VERSION,
                    "stateVersion": backfill.STATE_VERSION,
                    "format": "csv",
                    "columns": "0000deadbeef0000",
                    "start": "2026-06-01",
                    "end": "2026-09-20",
                    "lastDay": "2026-09-20",
                    "resumeFrom": None,
                    "complete": True,
                }
            ),
            encoding="utf-8",
        )
        archive = _Archive()
        assert _run(archive, tmp_path, "csv") == 1
        assert archive.calls == []
        assert (tmp_path / "p.csv").read_text(encoding="utf-8") == "old,header\n1,2\n"
        assert "column layout changed" in capsys.readouterr().err


class TestAStateWithNoFileStartsAgain:
    def test_a_deleted_data_file_is_downloaded_again_not_resumed_into(self, tmp_path: Path) -> None:
        # The state describes a file that is no longer there. Continuing would
        # write a file that starts half-way through and record it as complete.
        archive = _Archive()
        _run(archive, tmp_path)
        (tmp_path / "p.ndjson").unlink()
        archive.advance(1)
        assert _run(archive, tmp_path) == 0
        assert archive.calls[-1][0] == "2026-06-01"
        rows = _rows(tmp_path)
        assert min(r["date"] for r in rows) == "2026-06-01"
        _assert_every_row_final_and_unique(rows)


def test_a_csv_line_round_trips_through_the_csv_module() -> None:
    # `_trim_after` relies on csv.reader reading back exactly what `_csv_line`
    # wrote. Pinned directly, for the characters that need quoting.
    cells = {c: "" for c in backfill.CSV_COLUMNS}
    cells.update({"parkName": 'a,"b"\r\nc', "date": "2026-09-01"})
    line = backfill._csv_line(cells)
    parsed = next(csv.reader(io.StringIO(line, newline="")))
    assert ",".join(backfill._csv_cell(v) for v in parsed) + "\n" == line
