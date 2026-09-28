"""The packaged backfill command, driven against a stub client.

WHY THIS EXISTS, AND WHY IT FAILED ONCE ALREADY. This is the first code a paying
customer runs. Nothing tested it until 2026-09-28, when the first real customer
hit a nine-frame traceback on their first attempt: the range started at
`span.archive_from` (what the archive holds) while their Pro key reached back 400
days, so the FIRST request was refused.

Then the first version of these tests PASSED against a broken fix. The recovery
read `earliestAllowedDate` off the top level of the error body; the API nests it
under `error`. The fixture had been built from the formatted text in the
customer's traceback rather than from a real response, so the code and the test
shared one wrong assumption and agreed with each other. It shipped doing nothing.

So the body below is captured from production verbatim, and TestFixtureIsReal
fails if anyone flattens it. A fixture invented by the same person as the code
proves only that the two are consistent.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from themeparks import APIError, BudgetExhaustedError, RateLimitError, backfill
from themeparks._ergonomic.history import EntityRef, HistorySpan
from themeparks._generated.models import HistoryDailyRow, HistoryErrorWindowExceeded
from themeparks._transport import _format_error_message
from themeparks.backfill import _Park

# CAPTURED FROM PRODUCTION, 2026-09-28: anonymous GET of
# /v1/entity/7340550b-c14d-4def-80bb-acdb51d49a66/history/daily starting 2021-07-03.
PRODUCTION_403_BODY = {
    "error": {
        "type": "HISTORY_WINDOW_EXCEEDED",
        "message": "Requests without an API key can see history back to 2026-09-22 (7 days).",
        "earliestAllowedDate": "2026-09-22",
    }
}


def _window_403(earliest: str = "2025-08-25") -> APIError:
    """The same shape as production, with a Pro-sized window."""
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
        url="https://api.themeparks.wiki/v1/entity/x/history/daily",
    )


def _row(day: str, *, standby: bool = True) -> HistoryDailyRow:
    """A REAL row, from the generated model, not a stand-in with one attribute.

    The previous stub had `date` and `model_dump()` and nothing else, while
    `_csv_row` reads nine fields plus five nested stats. So `--format csv` raised
    AttributeError the moment a test touched it -- the CSV path was not merely
    untested, it was untestable, and it shipped writing a duplicate header.

    Building it from `HistoryDailyRow` means a spec change that renames a field
    fails these tests instead of a customer's first run.
    """
    return HistoryDailyRow.model_validate(
        {
            "date": day,
            "firstOperatingAt": f"{day}T13:00:00Z",
            "lastClosedAt": f"{day}T23:00:00Z",
            "operatingMinutes": 600,
            "downMinutes": 0,
            "standby": {"min": 5, "p50": 15, "mean": 17, "p90": 30, "max": 45} if standby else None,
            "singleRider": None,
            "showCount": None,
            "changes": 3,
        }
    )


class _History:
    """Refuses any start before `floor`, exactly as the API does."""

    def __init__(self, archive_from: str, through: str, floor: str | None) -> None:
        self.archive_from = archive_from
        self.through = through
        # DISTINCT from `through` by default. Giving both the same value is what
        # made a swap between them undetectable.
        self.recorded_to = through
        self.floor = floor
        self.calls: list[str] = []
        self.ends: list[str] = []

    def span(self) -> HistorySpan:
        return HistorySpan(
            date.fromisoformat(self.archive_from),
            date.fromisoformat(self.recorded_to),
            date.fromisoformat(self.through),
        )

    def days_with_entities(self, start, end):
        """Mirrors the real signature: the NAME comes from the response.

        Not `days()`. The command switched to `days_with_entities` so a row is
        labelled with the name the history response gave for it, rather than the
        park's current children list -- rides get renamed and old rows must keep
        the name they were recorded under.
        """
        self.calls.append(str(start))
        self.ends.append(str(end))
        if self.floor is not None and str(start) < self.floor:
            raise _window_403(self.floor)
        yield (EntityRef("ent-1", "Test Coaster", "ATTRACTION"), _row(str(start)))


class _Entity:
    def __init__(self, history: _History) -> None:
        self.history = history


class _Client:
    def __init__(self, history: _History) -> None:
        self._history = history

    def entity(self, _id: str) -> _Entity:
        return _Entity(self._history)


class TestFixtureIsReal:
    """Check the fixtures against something this repo did not hand-write.

    THE PREVIOUS VERSION OF THIS CLASS WAS A TAUTOLOGY. It asserted that
    PRODUCTION_403_BODY had an `error` key -- about a dict literal declared
    seventy lines above it, by the same author, in the same file. It could not
    fail for any change to the code, and it would NOT have caught the original
    bug: someone who believed the body was flat would have written a flat literal
    and asserted flatness, and it would have been green.

    `HistoryErrorWindowExceeded` is generated from the published OpenAPI spec by
    scripts/regenerate.py. Nobody here wrote it, and it REJECTS the flat shape.
    That is the difference between a test and a comment with `assert` in front.
    """

    def test_the_fixtures_match_the_published_schema(self) -> None:
        HistoryErrorWindowExceeded.model_validate(PRODUCTION_403_BODY)
        HistoryErrorWindowExceeded.model_validate(_window_403().body)

    def test_the_schema_rejects_the_shape_the_first_fix_assumed(self) -> None:
        # The whole bug in one assertion: the flat body is not a thing the API
        # sends, and the spec knows it.
        with pytest.raises(ValidationError):
            HistoryErrorWindowExceeded.model_validate(dict(PRODUCTION_403_BODY["error"]))

    def test_the_traceback_is_why_a_flat_fixture_looked_right(self) -> None:
        # _format_error_message unwraps the envelope for the human-readable
        # message, so a traceback shows the inner keys at the top level. Anyone
        # building a fixture from traceback text builds a flat one. Pinned so the
        # cause is recorded, not just the symptom.
        rendered = _format_error_message(403, "Forbidden", PRODUCTION_403_BODY)
        assert "earliestAllowedDate" in rendered
        assert "'error'" not in rendered

    def test_the_real_body_is_understood(self) -> None:
        exc = APIError("403", status=403, body=PRODUCTION_403_BODY, url="u")
        assert backfill._window_floor(exc) == "2026-09-22"

    def test_a_flattened_body_still_works(self) -> None:
        # Tolerated deliberately, so this cannot break again if the envelope is
        # ever unwrapped upstream.
        flat = dict(PRODUCTION_403_BODY["error"])
        exc = APIError("403", status=403, body=flat, url="u")
        assert backfill._window_floor(exc) == "2026-09-22"


class TestWindowFloor:
    def test_reads_the_earliest_allowed_date(self) -> None:
        assert backfill._window_floor(_window_403("2025-08-25")) == "2025-08-25"

    def test_ignores_a_403_that_is_not_a_window_error(self) -> None:
        # Retrying a forbidden-for-another-reason would resend the identical
        # request and fail identically.
        #
        # The body carries earliestAllowedDate ON PURPOSE. Without it the
        # function returns None whether the type check exists or not, so the
        # earlier version of this test passed with the guard deleted.
        other = APIError(
            "403",
            status=403,
            body={"error": {"type": "FORBIDDEN", "earliestAllowedDate": "2025-08-25"}},
            url="u",
        )
        assert backfill._window_floor(other) is None

    @pytest.mark.parametrize(
        "body",
        [
            None,
            "a string",
            {},
            {"error": {}},
            {"error": "a string"},
            {"error": {"type": "HISTORY_WINDOW_EXCEEDED"}},
            {"error": {"type": "HISTORY_WINDOW_EXCEEDED", "earliestAllowedDate": ""}},
        ],
    )
    def test_survives_a_body_it_cannot_read(self, body) -> None:
        # The body is whatever the server sent. Crashing while handling a crash
        # would be worse than the original traceback.
        assert backfill._window_floor(APIError("x", status=403, body=body, url="u")) is None


class TestBackfillPark:
    def test_restarts_at_the_plan_floor_instead_of_raising(self, tmp_path: Path, capsys) -> None:
        # The launch-day shape exactly: five years of archive, a 400-day key.
        hist = _History(archive_from="2021-07-03", through="2026-09-28", floor="2025-08-25")
        status = backfill.backfill_park(
            _Client(hist), _Park("park-1", "Park One"), tmp_path, "ndjson"
        )

        assert status == 0
        assert hist.calls == ["2021-07-03", "2025-08-25"]
        assert "reaches back to 2025-08-25" in capsys.readouterr().err
        assert (tmp_path / "park-1.ndjson").read_text().strip() != ""

    def test_does_not_retry_once_rows_are_written(self, tmp_path: Path) -> None:
        # A 403 mid-stream is not a plan boundary. Restarting would append days
        # already in the file, and the checkpoint design works hard to bound
        # duplication to a single day.
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        calls = {"n": 0}

        def days_with_entities(start, end):
            calls["n"] += 1
            yield (EntityRef("ent-1", "Test Coaster", "ATTRACTION"), _row("2025-01-01"))
            raise _window_403("2025-08-25")

        hist.days_with_entities = days_with_entities  # type: ignore[assignment]
        with pytest.raises(APIError):
            backfill.backfill_park(_Client(hist), _Park("park-2", "Park Two"), tmp_path, "ndjson")
        assert calls["n"] == 1

    def test_no_403_means_one_request_and_no_message(self, tmp_path: Path, capsys) -> None:
        # Business reaches the whole archive, so the common case must not pay for
        # this handling with an extra call or a confusing line of output.
        hist = _History(archive_from="2021-07-03", through="2026-09-28", floor=None)
        assert (
            backfill.backfill_park(_Client(hist), _Park("park-3", "Park Three"), tmp_path, "ndjson")
            == 0
        )
        assert hist.calls == ["2021-07-03"]
        assert "reaches back to" not in capsys.readouterr().err


class TestSpanBoundsTheEnd:
    """Three DISTINCT dates, so the two that used to be indistinguishable are not.

    The stub gave `recorded_to` and `retrievable_through` the same value, which
    made a whole class of bug invisible: swapping the one the command reads passed
    every test. `test_history.py` next door keeps those two dates a month apart on
    purpose, with the comment "A backfill bounded by recorded_to walks past the
    entitlement and into 403s". This is that guard, for this command.
    """

    def test_the_end_is_what_the_key_may_read_not_what_is_filed(self, tmp_path: Path) -> None:
        hist = _History(archive_from="2021-07-03", through="2026-08-23", floor=None)
        hist.recorded_to = "2026-09-22"  # a month LATER than retrievable_through
        backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        # `end` must be retrievable_through. Reading recorded_to would ask for a
        # month the key cannot see and earn a 403 on the last page.
        assert hist.ends == ["2026-08-23"]


class TestCsvOutput:
    """The CSV path, which had NO test at all while it corrupted every file."""

    def test_exactly_one_header_even_through_the_403_recovery(self, tmp_path: Path) -> None:
        # The recovery path is the NORMAL path for every plan short of the full
        # archive, and it used to write a second header row. pandas then read that
        # row as data and every numeric column came back as text.
        hist = _History(archive_from="2021-07-03", through="2026-09-28", floor="2025-08-25")
        backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "csv")
        text = (tmp_path / "p.csv").read_text(encoding="utf-8")
        assert sum(1 for line in text.splitlines() if line.startswith("parkId,")) == 1

    def test_identity_columns_come_first_and_carry_the_response_name(self, tmp_path: Path) -> None:
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        backfill.backfill_park(_Client(hist), _Park("park-id", "Park Name"), tmp_path, "csv")
        rows = list(
            csv.DictReader((tmp_path / "park-id.csv").read_text(encoding="utf-8").splitlines())
        )
        assert rows[0]["parkId"] == "park-id"
        assert rows[0]["parkName"] == "Park Name"
        assert rows[0]["entityId"] == "ent-1"
        # From the history response, NOT from a current children lookup: a ride
        # renamed later must keep the name its rows were recorded under.
        assert rows[0]["entityName"] == "Test Coaster"
        assert rows[0]["entityType"] == "ATTRACTION"
        assert list(rows[0])[:5] == [
            "parkId",
            "parkName",
            "entityId",
            "entityName",
            "entityType",
        ]


class TestRerunIsSafe:
    """The defect that silently doubled a customer's data."""

    def _run(self, tmp_path: Path, **kw) -> tuple[int, int]:
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson", **kw)
        return code, len((tmp_path / "p.ndjson").read_text(encoding="utf-8").strip().splitlines())

    def test_a_second_identical_run_does_not_append(self, tmp_path: Path) -> None:
        first_code, first_lines = self._run(tmp_path)
        second_code, second_lines = self._run(tmp_path)
        assert (first_code, second_code) == (0, 0)
        assert second_lines == first_lines, "re-running appended a second copy"

    def test_overwrite_replaces_rather_than_appends(self, tmp_path: Path) -> None:
        _, first_lines = self._run(tmp_path)
        _, again = self._run(tmp_path, overwrite=True)
        assert again == first_lines

    def test_completion_is_recorded_not_inferred(self, tmp_path: Path) -> None:
        # "Done" used to be the ABSENCE of a checkpoint, which is the same thing
        # as "never started". That ambiguity is what let a retry re-download a
        # finished park in full.
        self._run(tmp_path)
        state = json.loads((tmp_path / f"p{backfill.STATE_SUFFIX}").read_text(encoding="utf-8"))
        assert state["complete"] is True
        assert state["format"] == "ndjson"
        assert state["start"] == "2025-01-01"

    def test_a_file_we_did_not_write_is_refused(self, tmp_path: Path) -> None:
        # Appending would double someone's data; truncating would destroy it.
        (tmp_path / "p.ndjson").write_text('{"someone": "else"}\n', encoding="utf-8")
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        assert code == 1
        assert (tmp_path / "p.ndjson").read_text(encoding="utf-8") == '{"someone": "else"}\n'


class TestEmptyWindow:
    """A park whose data ended before the window opens. Used to be a traceback."""

    def test_skips_with_a_sentence_and_leaves_no_empty_file(self, tmp_path: Path, capsys) -> None:
        # Typhoon Lagoon's shape: data ends 2026-09-09, the floor clamps to
        # 2026-09-22, so from > to and the API answered 400 INVALID_RANGE. It
        # killed a six-park destination run three parks in.
        hist = _History(archive_from="2022-05-11", through="2026-09-09", floor="2026-09-22")
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        assert code == 0
        assert "nothing in your window" in capsys.readouterr().err
        assert not (tmp_path / "p.ndjson").exists(), "left a 0-byte file behind"


class TestNdjsonPayload:
    def test_every_key_is_asserted_not_just_non_emptiness(self, tmp_path: Path) -> None:
        # The old assertion was `read_text().strip() != ""`, which passes for two
        # CSV headers and no rows, or a row with an empty entityId.
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        backfill.backfill_park(_Client(hist), _Park("park-id", "Park Name"), tmp_path, "ndjson")
        line = json.loads((tmp_path / "park-id.ndjson").read_text(encoding="utf-8").splitlines()[0])
        row = _row("2025-01-01")
        assert line == {
            "parkId": "park-id",
            "parkName": "Park Name",
            "entityId": "ent-1",
            "entityName": "Test Coaster",
            "entityType": "ATTRACTION",
            **row.model_dump(mode="json"),
        }


class TestBudgetExhaustion:
    """Exit 75 and the state it leaves. Neither had any test."""

    def _hist_that_runs_out(self) -> _History:
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)

        def days_with_entities(start, end):
            hist.calls.append(str(start))
            # DELIBERATELY out of order, and the second entity's days end EARLIER.
            # That is the real shape: `_daily_rows` walks entities and then each
            # entity's days, so the final row belongs to the alphabetically last
            # entity, which may have stopped reporting mid-page. If the rows were
            # in ascending order, "furthest day" and "last row" would be the same
            # value and this test could not tell them apart.
            yield (EntityRef("ent-1", "Coaster", "ATTRACTION"), _row("2025-01-01"))
            yield (EntityRef("ent-1", "Coaster", "ATTRACTION"), _row("2025-03-09"))
            yield (EntityRef("ent-2", "Closed Ride", "ATTRACTION"), _row("2025-02-14"))
            raise BudgetExhaustedError("429", status=429, body={}, url="u", retry_after=2700.0)

        hist.days_with_entities = days_with_entities  # type: ignore[assignment]
        return hist

    def test_returns_ex_tempfail_so_a_scheduler_retries(self, tmp_path: Path) -> None:
        hist = self._hist_that_runs_out()
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        assert code == backfill.EX_TEMPFAIL == 75

    def test_records_the_furthest_day_so_a_rerun_continues(self, tmp_path: Path) -> None:
        hist = self._hist_that_runs_out()
        backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        state = json.loads((tmp_path / f"p{backfill.STATE_SUFFIX}").read_text(encoding="utf-8"))
        assert state["complete"] is False
        # The MAX day seen, not the last row yielded. The stub's final row is
        # 2025-02-14, so recording "last" instead of "max" would rewind the resume
        # point by three weeks and re-download them.
        assert state["last_day"] == "2025-03-09"

    def test_a_spent_budget_on_the_coverage_call_also_returns_75(self, tmp_path: Path) -> None:
        # span() is the FIRST request a resumed run makes, while the hourly window
        # is still shut. It used to sit outside the handler, so this path exited 1
        # with a traceback and a scheduler alerted instead of retrying.
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)

        def span():
            raise RateLimitError("429", status=429, body={}, url="u", retry_after=2700.0)

        hist.span = span  # type: ignore[assignment]
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        assert code == backfill.EX_TEMPFAIL


class TestEmptyWindowBeforeAnyRequest:
    def test_skips_without_making_a_request_at_all(self, tmp_path: Path, capsys) -> None:
        # The pre-clamp guard, which the post-clamp one cannot cover: a park whose
        # data ends before a resume point already known to be later.
        hist = _History(archive_from="2026-09-30", through="2026-09-09", floor=None)
        code = backfill.backfill_park(_Client(hist), _Park("p", "P"), tmp_path, "ndjson")
        assert code == 0
        assert hist.calls == [], "asked the API for a range it had already ruled out"
        assert "nothing in your window" in capsys.readouterr().err
