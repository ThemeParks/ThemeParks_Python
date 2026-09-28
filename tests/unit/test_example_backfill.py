"""The backfill recipe, driven against a stub client.

WHY THIS EXISTS. `examples/backfill.py` is the recipe the /api/recipes page
links to for "download the archive", so it is the first code a paying customer
runs. Nothing tested it, and on 2026-09-28 the first real customer hit a
nine-frame traceback on their first attempt: the script started at
`span.archive_from` (what the archive holds) while their Pro key could only
reach back 400 days, so the FIRST request returned 403 HISTORY_WINDOW_EXCEEDED.

The example is not importable as a module name (`examples/` is not a package),
so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from themeparks import APIError
from themeparks._ergonomic.history import HistorySpan

SPEC = importlib.util.spec_from_file_location(
    "backfill_example",
    Path(__file__).resolve().parents[2] / "examples" / "backfill.py",
)
assert SPEC and SPEC.loader
backfill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backfill)


def _window_403(earliest: str = "2025-08-25") -> APIError:
    return APIError(
        f"403 Forbidden: This key can see history back to {earliest}.",
        status=403,
        body={
            "type": "HISTORY_WINDOW_EXCEEDED",
            "message": f"This key can see history back to {earliest} (400 days).",
            "earliestAllowedDate": earliest,
        },
        url="https://api.themeparks.wiki/v1/entity/x/history/daily",
    )


class _Row:
    """The one field the writer and the checkpoint need."""

    def __init__(self, day: str) -> None:
        self.date = date.fromisoformat(day)

    def model_dump(self, mode: str = "json") -> dict:
        return {"date": self.date.isoformat()}


class _History:
    """Raises the window 403 for any start before `floor`, else yields a row."""

    def __init__(self, archive_from: str, through: str, floor: str | None) -> None:
        self.archive_from = archive_from
        self.through = through
        self.floor = floor
        self.calls: list[str | None] = []

    def span(self):
        return HistorySpan(
            date.fromisoformat(self.archive_from),
            date.fromisoformat(self.through),
            date.fromisoformat(self.through),
        )

    def days(self, start, end):
        self.calls.append(str(start))
        if self.floor is not None and str(start) < self.floor:
            raise _window_403(self.floor)
        yield ("ent-1", _Row(str(start)))


class _Entity:
    def __init__(self, history: _History) -> None:
        self.history = history


class _Client:
    def __init__(self, history: _History) -> None:
        self._history = history

    def entity(self, _id: str) -> _Entity:
        return _Entity(self._history)


class TestWindowFloor:
    def test_reads_the_earliest_allowed_date_out_of_a_403(self) -> None:
        assert backfill._window_floor(_window_403("2025-08-25")) == "2025-08-25"

    def test_ignores_a_403_that_is_not_a_window_error(self) -> None:
        # A forbidden-for-another-reason must not be retried as if the plan were
        # the problem: the retry would send the same request and fail again.
        other = APIError("403", status=403, body={"type": "FORBIDDEN"}, url="u")
        assert backfill._window_floor(other) is None

    @pytest.mark.parametrize("body", [None, "a string", {}, {"type": "HISTORY_WINDOW_EXCEEDED"}])
    def test_survives_a_body_it_cannot_read(self, body) -> None:
        # The body is whatever the server sent. A recipe that crashed while
        # handling a crash would be worse than the original traceback.
        assert backfill._window_floor(APIError("x", status=403, body=body, url="u")) is None


class TestBackfillPark:
    def test_restarts_at_the_plan_floor_instead_of_raising(self, tmp_path: Path, capsys) -> None:
        # The exact shape of the launch-day failure: five years of archive, a key
        # that reaches back 400 days.
        hist = _History(archive_from="2021-07-03", through="2026-09-28", floor="2025-08-25")
        status = backfill.backfill_park(_Client(hist), "park-1", tmp_path, "ndjson")

        assert status == 0
        # asked from the archive start, was refused, asked again from the floor
        assert hist.calls == ["2021-07-03", "2025-08-25"]
        assert "reaches back to 2025-08-25" in capsys.readouterr().err
        assert (tmp_path / "park-1.ndjson").read_text().strip() != ""

    def test_does_not_retry_once_rows_are_written(self, tmp_path: Path) -> None:
        # A 403 mid-stream is not a plan boundary. Restarting would append the
        # same days again, and a duplicate is the one thing the checkpoint design
        # goes out of its way to bound to a single day.
        hist = _History(archive_from="2025-01-01", through="2026-09-28", floor=None)
        calls = {"n": 0}

        def days(start, end):
            calls["n"] += 1
            yield ("ent-1", _Row("2025-01-01"))
            raise _window_403("2025-08-25")

        hist.days = days  # type: ignore[assignment]
        with pytest.raises(APIError):
            backfill.backfill_park(_Client(hist), "park-2", tmp_path, "ndjson")
        assert calls["n"] == 1

    def test_no_403_means_one_request_and_no_message(self, tmp_path: Path, capsys) -> None:
        # Business reaches the whole archive, so the common case must not pay for
        # this handling with an extra call or a confusing line of output.
        hist = _History(archive_from="2021-07-03", through="2026-09-28", floor=None)
        assert backfill.backfill_park(_Client(hist), "park-3", tmp_path, "ndjson") == 0
        assert hist.calls == ["2021-07-03"]
        assert "reaches back to" not in capsys.readouterr().err
