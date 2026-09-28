#!/usr/bin/env python3
"""Pull a park's whole daily history into a file, and survive the budget.

    python examples/backfill.py 7340550b-c14d-4def-80bb-acdb51d49a66
    python examples/backfill.py --format csv PARK_ID_A PARK_ID_B

The key comes from --api-key or the THEMEPARKS_API_KEY environment variable.

Three things this demonstrates that are easy to get wrong by hand:

1. It asks the PARK, not the rides. Both history endpoints answer every entity
   in a park in one request, so a park-level backfill of a large resort is
   around a hundred times fewer calls than the same data pulled ride by ride.

2. It bounds the range with `span().retrievable_through`, not with what the
   archive holds. Those are different dates on every plan below Business, and
   asking past the entitlement is how a long backfill ends in 403s.

3. It checkpoints. The history budget is hourly, so a spent one can be most of
   an hour from resetting. The SDK raises BudgetExhaustedError rather than
   sleeping through that; this writes down the last day it wrote and exits 75
   (EX_TEMPFAIL), which is the exit code to make a cron or a systemd timer
   retry rather than alert.

Re-running picks up from the checkpoint. It re-reads the last day on purpose:
a page can end mid-day, and one duplicate day is cheaper to de-duplicate than
a missing one is to notice.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, TextIO

from themeparks import APIError, BudgetExhaustedError, ThemeParks

EX_TEMPFAIL = 75

CSV_COLUMNS = [
    "entityId",
    "date",
    "firstOperatingAt",
    "lastClosedAt",
    "operatingMinutes",
    "downMinutes",
    "showCount",
    "changes",
    "standbyMin",
    "standbyP50",
    "standbyMean",
    "standbyP90",
    "standbyMax",
    "singleRiderP50",
    "singleRiderMax",
]


def _csv_row(entity_id: str, row: Any) -> dict[str, Any]:
    """Flatten the nested standby/singleRider stats into one wide row."""
    standby = row.standby
    single = row.singleRider
    return {
        "entityId": entity_id,
        "date": row.date.isoformat(),
        "firstOperatingAt": row.firstOperatingAt.isoformat() if row.firstOperatingAt else "",
        "lastClosedAt": row.lastClosedAt.isoformat() if row.lastClosedAt else "",
        "operatingMinutes": row.operatingMinutes,
        "downMinutes": row.downMinutes,
        "showCount": row.showCount if row.showCount is not None else "",
        "changes": row.changes,
        "standbyMin": standby.min if standby else "",
        "standbyP50": standby.p50 if standby else "",
        "standbyMean": standby.mean if standby else "",
        "standbyP90": standby.p90 if standby else "",
        "standbyMax": standby.max if standby else "",
        "singleRiderP50": single.p50 if single else "",
        "singleRiderMax": single.max if single else "",
    }


class Writer:
    """NDJSON or CSV behind one `write(entity_id, row)`."""

    def __init__(self, handle: TextIO, fmt: str, write_header: bool) -> None:
        self._handle = handle
        self._fmt = fmt
        self._csv = None
        if fmt == "csv":
            self._csv = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            if write_header:
                self._csv.writeheader()

    def write(self, entity_id: str, row: Any) -> None:
        if self._csv is not None:
            self._csv.writerow(_csv_row(entity_id, row))
            return
        payload = {"entityId": entity_id, **row.model_dump(mode="json")}
        self._handle.write(json.dumps(payload) + "\n")


def _window_floor(exc: APIError) -> str | None:
    """The earliest day this key may ask for, read out of a 403 body.

    The API knows the date exactly and says so:

        403 {"type": "HISTORY_WINDOW_EXCEEDED",
             "message": "This key can see history back to 2025-08-25 (400 days).",
             "earliestAllowedDate": "2025-08-25"}

    `/history/coverage` does not carry it. It reports `archiveFrom` (what we
    hold) and `retrievableThrough` (your ceiling) and no floor, so until it
    grows one, the 403 is the only place this number exists.
    """
    body = exc.body
    if not isinstance(body, dict) or body.get("type") != "HISTORY_WINDOW_EXCEEDED":
        return None
    floor = body.get("earliestAllowedDate")
    return floor if isinstance(floor, str) and floor else None


def backfill_park(tp: ThemeParks, park_id: str, out_dir: Path, fmt: str) -> int:
    """Write one park's daily history. Returns 0, or EX_TEMPFAIL if the budget ran out."""
    history = tp.entity(park_id).history
    span = history.span()

    out_path = out_dir / f"{park_id}.{'csv' if fmt == 'csv' else 'ndjson'}"
    checkpoint = out_dir / f"{park_id}.checkpoint"

    resuming = checkpoint.exists()
    has_rows = out_path.exists() and out_path.stat().st_size > 0
    start = checkpoint.read_text().strip() if resuming else span.archive_from
    end = span.retrievable_through

    print(
        f"{park_id}: {start} .. {end}{' (resumed)' if resuming else ''} -> {out_path}",
        file=sys.stderr,
    )

    # THE START IS THE ARCHIVE'S, AND THE ARCHIVE IS DEEPER THAN MOST PLANS.
    # `span.retrievable_through` bounds the END at what this key may reach, and
    # there is no matching field for the beginning: coverage reports
    # `archiveFrom` (what we hold) and `retrievableThrough` (your ceiling), with
    # no floor. So on any plan short of the full archive, `archive_from` is
    # usually before the first day this key may ask for, and the FIRST request
    # 403s. That is what happened to the first customer to run this, on Pro,
    # against a park holding five years.
    #
    # The 403 carries the floor, so this asks, is told, and starts again there.
    # One wasted request and a line of explanation, instead of a traceback.
    written = 0
    last_day = None

    def write_rows(handle: TextIO, first_day: str | date | None) -> None:
        """Stream one range into the file. Raises whatever the SDK raises."""
        nonlocal written, last_day
        writer = Writer(handle, fmt, write_header=not has_rows and written == 0)
        for entity_id, row in history.days(first_day, end):
            writer.write(entity_id, row)
            written += 1
            last_day = row.date
            if written % 5000 == 0:
                print(f"  {written} rows, at {last_day}", file=sys.stderr)

    try:
        with out_path.open("a", newline="") as handle:
            try:
                write_rows(handle, start)
            except APIError as exc:
                floor = _window_floor(exc)
                # Only retry when nothing was written: a 403 mid-stream is not a
                # plan boundary, and silently restarting would duplicate rows.
                if floor is None or written:
                    raise
                print(
                    f"  this key reaches back to {floor}, not {start} — starting there",
                    file=sys.stderr,
                )
                start = floor
                write_rows(handle, start)
    except BudgetExhaustedError as exc:
        if last_day is not None:
            checkpoint.write_text(last_day.isoformat())
        wait = exc.retry_after or 0
        print(
            f"  budget spent after {written} rows at {last_day}; rerun in {wait:.0f}s to continue",
            file=sys.stderr,
        )
        return EX_TEMPFAIL

    checkpoint.unlink(missing_ok=True)
    print(f"  done: {written} rows", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("park_ids", nargs="+", help="park entity ids")
    parser.add_argument("--api-key", default=os.environ.get("THEMEPARKS_API_KEY"))
    parser.add_argument("--format", choices=["ndjson", "csv"], default="ndjson")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error("no key: pass --api-key or set THEMEPARKS_API_KEY")

    args.out.mkdir(parents=True, exist_ok=True)

    # One client for every park: the connection pool and the cache are worth
    # reusing, and the budget is per account either way.
    with ThemeParks(api_key=args.api_key, user_agent="themeparks-backfill-example/1") as tp:
        for park_id in args.park_ids:
            status = backfill_park(tp, park_id, args.out, args.format)
            if status != 0:
                # Stop at the first exhausted budget. Carrying on to the next
                # park only spends the retry-after on 429s.
                return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
