"""Download a park's whole daily history to a file, and survive the budget.

Run it:

    themeparks-backfill "Disneyland Park"
    python -m themeparks.backfill "Disneyland Park"       # same thing

WHY THIS IS IN THE PACKAGE RATHER THAN AN EXAMPLE TO COPY. It used to be
`examples/backfill.py` on GitHub, linked from the docs. The first paying customer
followed that link and had to work out that the library needed installing, then
what the arguments were, then read a traceback. Every one of those is a wall
between someone who has paid for the archive and the archive. A recipe you have
to reconstruct is not a recipe; this is one command.

What it does that is easy to get wrong by hand:

1. It asks the PARK, not the rides. Both history endpoints answer every entity in
   a park in one request, so a park-level backfill of a large resort is around a
   hundred times fewer calls than the same data pulled ride by ride.

2. It bounds the range at BOTH ends. `span().retrievable_through` is the latest
   day your key may ask for. There is no field for the earliest -- coverage
   reports where the archive starts, which on any plan short of the full archive
   is before your window -- so the first request is refused and the floor is read
   out of that 403. See `_window_floor`.

3. It records what it has done, in `<park-id>.backfill-state.json`: the format,
   the range, the furthest day written, and whether it finished. The history
   budget is hourly, so a spent one can be most of an hour from resetting; the
   SDK raises BudgetExhaustedError rather than sleeping through that, and this
   writes the state and exits 75 (EX_TEMPFAIL) so a scheduler retries rather
   than alerts.

   Re-running is then safe in every direction: an unfinished park continues, a
   FINISHED park is left alone rather than appended to twice, and a file this
   command did not write is never touched without `--overwrite`. It re-reads the
   furthest day on purpose -- a page can end mid-day -- so `(entityId, date)` is
   the natural key if you load blind.

4. It takes NAMES as well as ids, and DESTINATIONS as well as parks. A
   customer has "Walt Disney World Resort", not four park uuids, and making
   them look those up first was another wall. A destination back fills every
   park in it, into one file each.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple, TextIO, Union

from themeparks import APIError, BudgetExhaustedError, RateLimitError, ThemeParks
from themeparks._ergonomic.history import EntityRef

USER_AGENT = "themeparks-backfill/1"

EX_TEMPFAIL = 75

CSV_COLUMNS = [
    # Identity first. A reader opening this in a spreadsheet should know what a
    # row is before they reach the numbers, and a table loaded from several files
    # needs parkId to tell them apart.
    "parkId",
    "parkName",
    "entityId",
    "entityName",
    "entityType",
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


def _csv_row(ref: EntityRef, row: Any, ident: _RowIdentity) -> dict[str, Any]:
    """Flatten the nested standby/singleRider stats into one wide row."""
    standby = row.standby
    single = row.singleRider
    return {
        "parkId": ident.park.id,
        "parkName": ident.park.name,
        "entityId": ref.id,
        "entityName": ref.name,
        "entityType": ref.entity_type,
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


class _Park(NamedTuple):
    """A park's identity, so rows can name themselves.

    `backfill_park` used to take only the id, so every row carried a bare
    `entityId` and nothing else. A customer loading two files into one table
    could not tell the parks apart -- the park id existed only in the FILENAME --
    and 60 attraction GUIDs with no labels meant writing the lookup code they
    bought this to avoid.
    """

    id: str
    name: str


class _RowIdentity(NamedTuple):
    """What a row inherits from the run, as opposed to from the response.

    Only the park. The entity's name and type come from the history response
    itself (`days_with_entities`), because those are per row and change over
    time: a ride renamed in 2024 must not have its 2021 rows relabelled with
    today's name. The park id is here rather than in the filename alone so two
    files can be loaded into one table.
    """

    park: _Park


class Writer:
    """NDJSON or CSV behind one `write(entity_id, row)`."""

    def __init__(self, handle: TextIO, fmt: str, write_header: bool, ident: _RowIdentity) -> None:
        self._handle = handle
        self._fmt = fmt
        self._ident = ident
        self._csv = None
        if fmt == "csv":
            self._csv = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            if write_header:
                self._csv.writeheader()

    def write(self, ref: EntityRef, row: Any) -> None:
        if self._csv is not None:
            self._csv.writerow(_csv_row(ref, row, self._ident))
            return
        # Identity keys come FIRST in the object, so a human reading one line of
        # NDJSON sees what it is before the numbers.
        payload = {
            "parkId": self._ident.park.id,
            "parkName": self._ident.park.name,
            "entityId": ref.id,
            "entityName": ref.name,
            "entityType": ref.entity_type,
            **row.model_dump(mode="json"),
        }
        self._handle.write(json.dumps(payload) + "\n")


def _use_utf8(*streams: TextIO) -> None:
    """Stop a legacy Windows code page killing the run on a park name.

    stdout's error handler is `strict`, and real names carry characters cp437,
    cp850 and cp932 cannot encode -- `Walt Disney World® Resort`,
    `LEGOLAND® Korea`, `Knott’s Soak City`, `Walibi Rhône-Alpes`. So `--list`
    died with UnicodeEncodeError partway through, after writing 200 good lines,
    whenever output was redirected on a non-1252 system. Redirecting is the
    obvious thing to do with 358 lines of parks.

    `reconfigure` is 3.7+; `backslashreplace` degrades an unencodable character
    to an escape instead of ending the run.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="backslashreplace")


def _window_floor(exc: APIError) -> str | None:
    """The earliest day this key may ask for, read out of a 403 body.

    THE BODY IS NESTED, and the first version of this function was not. What the
    API actually sends is:

        403 {"error": {"type": "HISTORY_WINDOW_EXCEEDED",
                       "message": "This key can see history back to 2025-08-25 (400 days).",
                       "earliestAllowedDate": "2025-08-25"}}

    The first version read those keys off the TOP level, because it was written
    from the formatted text in a traceback rather than from a real response. Its
    tests passed -- they built the fixture the same wrong way -- and it shipped
    doing nothing at all. `tests/unit/test_backfill.py` now pins a body captured
    from production, which is the only version of this test that can fail.

    Both shapes are accepted: the nested one the API sends, and a bare one, so
    this cannot break again if an error envelope is ever flattened.

    `/history/coverage` does not carry the floor -- it reports where the archive
    starts and where your window ends, and nothing in between -- so the 403 is
    the only place this date exists. Exposing it on the coverage document is
    tracked upstream.
    """
    body = exc.body
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    payload = inner if isinstance(inner, dict) else body
    if payload.get("type") != "HISTORY_WINDOW_EXCEEDED":
        return None
    floor = payload.get("earliestAllowedDate")
    return floor if isinstance(floor, str) and floor else None


# --------------------------------------------------------------------------
# Per-park state. One file, and it closes five separate defects.
# --------------------------------------------------------------------------
#
# The old scheme was a bare `<park-id>.checkpoint` holding one date, and "this
# park is finished" was encoded as THE ABSENCE of that file -- which is
# indistinguishable from "never started". The data file was always opened in
# append mode. Between them that produced:
#
#   - re-running a finished park appended a second complete copy, silently.
#     Measured: 382 rows became 764.
#   - a destination run that hit the hourly budget re-downloaded every COMPLETED
#     park in full on each retry, spending the new budget on work already done,
#     so a later park might never advance while the finished files grew by a
#     copy an hour.
#   - switching --format mid-resume wrote a new file starting at the checkpoint
#     day and silently lost everything before it, exit 0.
#   - a truncated or empty checkpoint sent `?from=&to=` forever, with no way to
#     know a hidden file was the cause.
#   - nothing recorded which range had been written, so nothing could tell.
#
# So state is explicit: the format, the range asked for, the high-water day, and
# whether it finished. Unreadable state is treated as no state rather than
# crashing -- a corrupt file must not be a permanent wall.
STATE_SUFFIX = ".backfill-state.json"


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, **fields: Any) -> None:
    path.write_text(json.dumps(fields, sort_keys=True) + "\n", encoding="utf-8")


class _StateFile(NamedTuple):
    """Where the state lives and the range it describes, fixed for one park."""

    path: Path
    fmt: str
    start: Day
    end: Day


def _record(sf: _StateFile, last_day: date | None, *, complete: bool) -> None:
    """Write the state file. `complete` is the fact the old checkpoint could not express.

    `sf.start` is the ORIGINAL start of the range, not the day a resumed run
    happened to begin at. The two call sites used to disagree about that, so a
    run interrupted twice recorded the second resume point as though it were the
    beginning and lost the real range.
    """
    _write_state(
        sf.path,
        format=sf.fmt,
        start=str(sf.start),
        end=str(sf.end),
        last_day=last_day.isoformat() if last_day else None,
        complete=complete,
    )


Day = Union[str, date, None]


def _is_empty_window(first_day: Day, end: Day) -> bool:
    """True when the plan's floor sits past the park's last day of data.

    `end` is the newest day the park has data for; the 403 recovery clamps the
    start UP to the first day this key may read. For a park that stopped
    reporting before the window opens -- a seasonal water park, a closed ride --
    the clamp can push start past end, and the API answers
    `400 INVALID_RANGE: to must not be before from`. That killed a six-park
    destination run three parks in, leaving a 0-byte file and two parks never
    attempted, on every plan.
    """
    return end is not None and first_day is not None and str(first_day) > str(end)


def _say_empty(end: Day, start: Day | None = None) -> None:
    reach = f", and your plan reaches back to {start}" if start is not None else ""
    print(
        f"  nothing in your window: this park's data ends {end}{reach} — skipping",
        file=sys.stderr,
    )


class _Plan(NamedTuple):
    """What a run should do about a park, once its state file has been read."""

    start: Day
    has_rows: bool
    prior_start: str | None


def _decide(
    out_path: Path, state_path: Path, fmt: str, overwrite: bool, archive_from: Day
) -> _Plan | int:
    """A `_Plan` to proceed with, or an exit code meaning "do not".

    Split out of `backfill_park` because it got long enough for ruff to object,
    and ruff was right: deciding whether to write is a different job from
    writing. Every branch here exists for a defect measured in review -- see the
    STATE block above for the five of them.
    """
    state = {} if overwrite else _read_state(state_path)
    if overwrite:
        out_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)

    file_exists = out_path.exists() and out_path.stat().st_size > 0
    same_format = state.get("format") == fmt

    # Finished already. Say so and stop, rather than appending a second copy.
    if state.get("complete") and same_format and file_exists:
        print(
            f"  already complete: {state.get('start')} .. {state.get('end')} "
            f"in {out_path.name} — pass --overwrite to fetch it again",
            file=sys.stderr,
        )
        return 0

    # A file we have no record of writing. Refusing is the only safe answer:
    # appending doubles it, truncating throws away someone's data.
    if file_exists and not state:
        print(
            f"  {out_path.name} already has rows and there is no state file beside it.\n"
            f"    --overwrite   replace it\n"
            f"    or move it aside and run again",
            file=sys.stderr,
        )
        return 1

    # A format switch cannot resume: the half-written file is the other format.
    if state and not same_format and not state.get("complete"):
        other = state.get("format")
        print(
            f"  {other} was interrupted part-way for this park. Finish it in "
            f"{other}, or pass --overwrite to start again in {fmt}",
            file=sys.stderr,
        )
        return 1

    resuming = bool(state) and same_format and not state.get("complete")
    last_written = state.get("last_day") if resuming else None
    return _Plan(
        start=last_written or archive_from,
        has_rows=file_exists and resuming,
        prior_start=state.get("start") if resuming else None,
    )


class _Job(NamedTuple):
    """Everything streaming one park needs, so the streamer takes two arguments."""

    history: Any
    out_path: Path
    fmt: str
    end: Day
    has_rows: bool
    ident: _RowIdentity


class _Progress:
    """How far the stream got. MUTABLE, and that is the point.

    `_stream` used to return this as a tuple, which meant an exception threw the
    numbers away: the budget handler then had nothing to record and read the state
    file back instead, which on a first run does not exist. The resume point was
    lost on exactly the interruption it exists for, and a re-run started over.

    Owned by the caller, updated in place, so it is readable after a raise.
    """

    def __init__(self) -> None:
        self.written = 0
        self.last_day: date | None = None
        self.skipped = False


def _stream(job: _Job, start: Day, progress: _Progress) -> None:
    """Write the range to the file, recovering once from a window 403.

    Separated from `backfill_park` because that function was deciding, printing,
    streaming and recording in one place, and ruff counted the statements before
    a reader had to. This is the streaming.
    """

    def write_rows(writer: Writer, first_day: Day) -> None:
        """Stream one range into the file. Raises whatever the SDK raises."""
        for ref, row in job.history.days_with_entities(first_day, job.end):
            writer.write(ref, row)
            progress.written += 1
            # MAX, not last-seen. `_daily_rows` walks entities and then each
            # entity's days, so the final row belongs to the alphabetically last
            # entity, which may have stopped reporting mid-page. Taking it as the
            # high-water mark could rewind the resume point by up to a whole
            # 31-day page, while the module claimed the overlap was "one day".
            progress.last_day = (
                row.date if progress.last_day is None else max(progress.last_day, row.date)
            )
            if progress.written % 5000 == 0:
                print(f"  {progress.written} rows, at {progress.last_day}", file=sys.stderr)

    with job.out_path.open("a", newline="", encoding="utf-8") as handle:
        # ONE Writer for the whole park, so the header decision is made once. It
        # used to be built inside write_rows with `written == 0` in the predicate,
        # and the recovery below calls that again precisely when written is 0 --
        # so every CSV on every plan short of the full archive got TWO headers,
        # and pandas read the second as data.
        writer = Writer(handle, job.fmt, not job.has_rows, job.ident)
        try:
            write_rows(writer, start)
        except APIError as exc:
            floor = _window_floor(exc)
            # Retry only when nothing was written: a 403 mid-stream is not a plan
            # boundary, and restarting would duplicate rows.
            if floor is None or progress.written:
                raise
            print(
                f"  this key reaches back to {floor}, not {start} — starting there",
                file=sys.stderr,
            )
            if _is_empty_window(floor, job.end):
                # Only visible after the clamp, and the file exists by now because
                # opening it created it. Flagged, not returned, so the empty file
                # is removed after the handle closes.
                _say_empty(job.end)
                progress.skipped = True
            else:
                write_rows(writer, floor)


def backfill_park(
    tp: ThemeParks, park: _Park, out_dir: Path, fmt: str, overwrite: bool = False
) -> int:
    """Write one park's daily history. Returns 0, or EX_TEMPFAIL if the budget ran out."""
    park_id = park.id
    history = tp.entity(park_id).history

    # SPAN IS INSIDE THE BUDGET HANDLING, and it was not.
    #
    # `coverage()` is the one history call the SDK does not wrap in
    # `_reraise_if_too_long`, so a spent hourly budget surfaces here as a plain
    # RateLimitError rather than BudgetExhaustedError. This call used to sit
    # outside the try, so that exception escaped `main` entirely: a nine-frame
    # traceback and exit 1.
    #
    # That is the MOST LIKELY path after any exit 75. The scheduler re-runs while
    # the hourly window is still shut, and this is the first request the resumed
    # run makes -- so the retry alerted instead of retrying, which is the exact
    # opposite of what exit 75 exists for. BudgetExhaustedError subclasses
    # RateLimitError, so one except covers both.
    try:
        span = history.span()
    except RateLimitError as exc:
        wait = getattr(exc, "retry_after", None) or 0
        print(
            f"{park_id}: history budget is spent; rerun the same command in "
            f"{wait / 60:.0f} min to continue",
            file=sys.stderr,
        )
        return EX_TEMPFAIL

    ext = "csv" if fmt == "csv" else "ndjson"
    out_path = out_dir / f"{park_id}.{ext}"
    state_path = out_dir / f"{park_id}{STATE_SUFFIX}"
    end = span.retrievable_through

    decided = _decide(out_path, state_path, fmt, overwrite, span.archive_from)
    if isinstance(decided, int):
        return decided
    start, has_rows, prior_start = decided
    resuming = prior_start is not None
    sf = _StateFile(state_path, fmt, prior_start or start, end)

    print(
        f"{park_id}: {start} .. {end}{' (resumed)' if resuming else ''} -> {out_path}",
        file=sys.stderr,
    )

    if _is_empty_window(start, end):
        _say_empty(end, start)
        out_path.unlink(missing_ok=True)
        return 0

    ident = _RowIdentity(park)
    job = _Job(history, out_path, fmt, end, has_rows, ident)
    progress = _Progress()
    try:
        _stream(job, start, progress)
    except BudgetExhaustedError as exc:
        # The budget is hourly, so a spent one can be most of an hour from
        # resetting. Record how far we got and exit 75 rather than sleeping.
        _record(sf, progress.last_day, complete=False)
        wait = exc.retry_after or 0
        print(
            f"  budget spent; rerun the same command in {wait / 60:.0f} min to continue",
            file=sys.stderr,
        )
        return EX_TEMPFAIL

    if progress.skipped and progress.written == 0:
        out_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        return 0

    # Completion is RECORDED, never inferred from a missing file. That is the
    # distinction the old checkpoint could not make.
    _record(sf, progress.last_day, complete=True)
    print(f"  done: {progress.written} rows -> {out_path}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# Finding what to back fill, without knowing any uuid.
# --------------------------------------------------------------------------


def _normalize(value: str) -> str:
    """Fold a name to something a person could plausibly have typed.

    THIS EXISTS BECAUSE THE DOCUMENTED EXAMPLE DID NOT WORK. Matching used bare
    `casefold()`, and the live name is `Walt Disney World® Resort`, so
    `themeparks-backfill "Walt Disney World Resort"` -- the command's own epilog
    example -- answered "no park or destination matching". Five live names were
    unreachable that way:

        Walt Disney World® Resort        U+00AE
        LEGOLAND® Korea                  U+00AE
        Walibi Rhône-Alpes               U+00F4
        Knott’s Soak City                U+2019, while its sibling in the SAME
                                         destination is ASCII Knott's Berry Farm

    NFKD splits an accented letter into letter plus combining mark, the mark is
    dropped, and every non-alphanumeric character goes -- so ®, apostrophes of
    either kind, spaces, hyphens and punctuation stop mattering. A side effect
    worth having: the URL slug form matches too, and slugs are what people copy
    out of an address bar.

    `_ergonomic/destinations.py` has a lighter version of this, written first.
    That is the one this should have reused.
    """
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(c for c in folded if c.isalnum() and not unicodedata.combining(c))


def _catalogue(tp: ThemeParks) -> list[tuple[str, str, str, str]]:
    """Every park as (park id, park name, destination id, destination name).

    One call to /destinations, which is public and cacheable, so this costs
    nothing worth optimising and works before you have a key at all -- which is
    the point: you can find your park before deciding whether to pay.
    """
    out: list[tuple[str, str, str, str]] = []
    # `tp.raw` is the generated client; the ergonomic surface has no
    # destinations call and does not need one for this.
    for dest in tp.raw.get_destinations().destinations:
        for park in dest.parks or []:
            out.append((park.id, park.name, dest.id, dest.name))
    return out


def _by_id(catalogue: list[tuple[str, str, str, str]], wanted: str) -> list[tuple[str, str]] | None:
    """Parks for an exact id, or None if `wanted` is not an id we know.

    A destination id is checked first and expands to its parks. Destination and
    park ids never collide, so the order is about being deliberate rather than
    about resolving a conflict.
    """
    in_destination = [(pid, pname) for pid, pname, did, _ in catalogue if did == wanted]
    if in_destination:
        return in_destination
    for pid, pname, _, _ in catalogue:
        if pid == wanted:
            return [(pid, pname)]
    if _looks_like_id(wanted):
        # An id we do not list: a park with no destination row, an attraction, or
        # a typo. Pass it through and let the API say which, rather than
        # second-guessing it here.
        return [(wanted, wanted)]
    return None


def _by_name(catalogue: list[tuple[str, str, str, str]], wanted: str) -> list[tuple[str, str]]:
    """Parks for a name, or SystemExit listing the candidates.

    Exact wins outright, so "Magic Kingdom Park" is not ambiguous merely because
    something else contains it. A destination name expands exactly as its id
    does. More than one match is an error: guessing between two parks would
    quietly download the wrong one and look like it worked, which is the worst
    outcome available here.
    """
    lowered = _normalize(wanted)

    def parks_in(did: str) -> list[tuple[str, str]]:
        return [(pid, pname) for pid, pname, d, _ in catalogue if d == did]

    exact_dest = {did: dname for _, _, did, dname in catalogue if _normalize(dname) == lowered}
    if len(exact_dest) == 1:
        return parks_in(next(iter(exact_dest)))

    exact_park = [(pid, pname) for pid, pname, _, _ in catalogue if _normalize(pname) == lowered]
    if len(exact_park) == 1:
        return exact_park

    park_hits = [(pid, pname) for pid, pname, _, _ in catalogue if lowered in _normalize(pname)]
    dest_hits = {did: dname for _, _, did, dname in catalogue if lowered in _normalize(dname)}
    if len(dest_hits) == 1 and not park_hits:
        return parks_in(next(iter(dest_hits)))

    # THE DESTINATION GOES IN THE LABEL, and it is load-bearing: TWO parks are
    # named exactly "Disneyland Park" -- Anaheim and Paris -- so a list of bare
    # park names offers a choice between two identical lines.
    dest_of = {pid: dname for pid, _, _, dname in catalogue}
    candidates = [(pid, f"{pname}  ({dest_of[pid]})") for pid, pname in park_hits] or [
        (did, f"{dname}  (destination, {len(parks_in(did))} parks)")
        for did, dname in dest_hits.items()
    ]
    if not candidates:
        raise SystemExit(
            f'no park or destination matching "{wanted}".\n'
            f"  themeparks-backfill --list           everything\n"
            f'  themeparks-backfill --list disney    the ones matching "disney"'
        )
    if len(candidates) > 1:
        lines = "\n".join(
            f"  {cid}  {name}" for cid, name in sorted(candidates, key=lambda c: c[1])
        )
        raise SystemExit(
            f'"{wanted}" matches {len(candidates)}. Pass an id, or the destination'
            f" name to get all of its parks:\n{lines}"
        )
    return candidates


def _resolve(catalogue: list[tuple[str, str, str, str]], wanted: str) -> list[tuple[str, str]]:
    """The parks to back fill, as (id, name), from whichever handle they have.

    Accepts four things, because a customer has whichever one they found:

      - a park id                  -> that park
      - a DESTINATION id           -> every park in it
      - a park name                -> that park
      - a destination name         -> every park in it

    "Walt Disney World Resort" is the shape people actually have, and making them
    look up four park uuids first was a wall for no reason.
    """
    by_id = _by_id(catalogue, wanted)
    return by_id if by_id is not None else _by_name(catalogue, wanted)


UUID_LENGTH = 36
UUID_DASHES = 4


def _looks_like_id(value: str) -> bool:
    """A uuid, loosely. Loose on purpose: the API decides what is valid, not us."""
    return len(value) == UUID_LENGTH and value.count("-") == UUID_DASHES


def _print_list(tp: ThemeParks, needle: str | None) -> int:
    """Parks grouped under their destination, so a destination id is visible too."""
    catalogue = _catalogue(tp)
    if needle:
        lowered = _normalize(needle)
        catalogue = [
            c for c in catalogue if lowered in _normalize(c[1]) or lowered in _normalize(c[3])
        ]
    if not catalogue:
        print(f'nothing matching "{needle}"', file=sys.stderr)
        return 1

    by_dest: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for pid, pname, did, dname in catalogue:
        by_dest.setdefault((did, dname), []).append((pid, pname))
    for (did, dname), parks in sorted(by_dest.items(), key=lambda kv: kv[0][1]):
        # The destination line is indented left of its parks and labelled, so it
        # reads as "pass this to get all of them" rather than as another park.
        print(f"{did}  {dname}  <- destination: all {len(parks)} parks")
        for pid, pname in sorted(parks, key=lambda p: p[1]):
            print(f"    {pid}  {pname}")
    return 0


EPILOG = """examples:
  themeparks-backfill "Disneyland Park"
      the whole daily history your plan reaches, as NDJSON, into the current
      directory

  themeparks-backfill "Walt Disney World Resort"
      a DESTINATION: every park in it, one file each

  themeparks-backfill --list disney
      find an id, or check the spelling. Lists destinations with their parks
      indented underneath. Works without a key.

  themeparks-backfill "Epcot" --format csv --out ./data
      one wide CSV row per entity per day, into ./data

  themeparks-backfill <id-a> <id-b> <id-c>
      several parks in one run, sharing one connection and one budget

exit codes:
  0   done
  75  the hourly history budget ran out. Progress is checkpointed; run the same
      command again to continue. This is EX_TEMPFAIL, so a cron or systemd timer
      retries instead of alerting.

how far back this reaches is your plan: 7 days with no key at all, 30 on a free
key, 400 on Pro, the whole archive on Business. It runs either way -- it asks the
API what you may see and starts there, so you never have to work it out, and it
never asks for a day you are not entitled to twice.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="themeparks-backfill",
        description="Download a park's daily history to a file."
        " One request per page, checkpointed, resumable.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "parks",
        nargs="*",
        metavar="PARK",
        help="park or DESTINATION, by name or id. A destination back fills every park in it.",
    )
    parser.add_argument(
        "--list",
        nargs="?",
        const="",
        metavar="TEXT",
        dest="list_parks",
        help="list ids and names, optionally filtered, then exit. Needs no key.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("THEMEPARKS_API_KEY"),
        help="API key. Defaults to $THEMEPARKS_API_KEY.",
    )
    parser.add_argument(
        "--format",
        choices=["ndjson", "csv"],
        default="ndjson",
        help="output format (default: ndjson)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing file instead of refusing. Without it, a park"
        " that finished is not fetched twice and a file this command did not"
        " write is never touched.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="output directory (default: .)",
    )
    args = parser.parse_args(argv)

    _use_utf8(sys.stdout, sys.stderr)

    # --list first: it is how you find a park, so it must work before you have a
    # key and before you have decided to pay for anything.
    if args.list_parks is not None:
        with ThemeParks(api_key=args.api_key, user_agent=USER_AGENT) as tp:
            return _print_list(tp, args.list_parks or None)

    if not args.parks:
        parser.error("which park or destination? try: themeparks-backfill --list disney")

    # NO KEY IS NOT AN ERROR. It used to be: the command refused to start, with a
    # message that said in the same breath that anonymous access reads 7 days.
    # Telling someone the thing works and then declining to do it is worse than
    # either. Anonymous reads 7 days, so it runs, says so, and says what a key
    # would add -- which is also the honest sales pitch: the person evaluating
    # whether to pay is exactly the person who should be able to run this.
    if not args.api_key:
        print(
            "no API key: reading the 7 days anonymous access allows.\n"
            "  a free key reads 30 days, Pro 400, Business the whole archive\n"
            "  set THEMEPARKS_API_KEY, or pass --api-key\n"
            "  keys: https://www.themeparks.wiki/profile\n",
            file=sys.stderr,
        )

    args.out.mkdir(parents=True, exist_ok=True)

    # One client for every park: the connection pool is worth reusing and the
    # budget is per account either way.
    with ThemeParks(api_key=args.api_key, user_agent=USER_AGENT) as tp:
        catalogue = _catalogue(tp)
        dest_of = {pid: dname for pid, _, _, dname in catalogue}
        targets: list[tuple[str, str]] = []
        seen: set[str] = set()
        for wanted in args.parks:
            for pid, pname in _resolve(catalogue, wanted):
                # A destination and one of its parks can both be named on one
                # command line. Back filling the same park twice would double
                # every row in the file.
                if pid not in seen:
                    seen.add(pid)
                    targets.append((pid, pname))

        # ALWAYS ECHO WHAT A NAME RESOLVED TO, even for a single park, and tell
        # the caller to use the id next time.
        #
        # Names are for FINDING a park once. Ids are for asking for it. Twelve
        # live parks contain "Hurricane Harbor" and the bare name is an exact
        # match for the St. Louis one, so someone in Chicago could have typed a
        # reasonable thing, got no warning, and loaded another park's history
        # believing it was theirs. Echoing the resolution is what makes that
        # visible; recommending the id is what stops it recurring in a script.
        resolved_by_name = any(not _looks_like_id(w) for w in args.parks)
        if len(targets) > 1:
            print(f"{len(targets)} parks to back fill:", file=sys.stderr)
            for pid, pname in targets:
                where = dest_of.get(pid)
                suffix = f"  ({where})" if where and where != pname else ""
                print(f"  {pname}{suffix}  {pid}", file=sys.stderr)
        elif resolved_by_name:
            pid, pname = targets[0]
            where = dest_of.get(pid)
            suffix = f"  ({where})" if where and where != pname else ""
            print(f"resolved to {pname}{suffix}  {pid}", file=sys.stderr)
        if resolved_by_name:
            ids = " ".join(pid for pid, _ in targets)
            print(
                f"  use the id next time — names are convenient once, ids are exact:\n"
                f"    themeparks-backfill {ids}",
                file=sys.stderr,
            )

        for park_id, pname in targets:
            status = backfill_park(tp, _Park(park_id, pname), args.out, args.format, args.overwrite)
            if status != 0:
                # Stop at the first exhausted budget. Carrying on to the next
                # park only spends the retry-after on 429s.
                return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
