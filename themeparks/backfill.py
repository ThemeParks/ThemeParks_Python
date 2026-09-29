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
   FINISHED park is brought up to date from the day after its last one rather
   than appended to twice, and a file this command did not write is never
   touched without `--overwrite`. `(entityId, date)` is the natural key if you
   load blind: a run that died inside its first page resumes on the last day it
   wrote, so that one day can appear twice.

4. It takes NAMES as well as ids, and DESTINATIONS as well as parks. A
   customer has "Walt Disney World Resort", not four park uuids, and making
   them look those up first was another wall. A destination back fills every
   park in it, into one file each.

5. It writes FINAL days only. Today's row is the day so far, and the archive
   records days 2 to 3 behind live data, so the newest days the API serves can
   still change. The run ends at `span().final_through`, the newest day the
   archive holds, and the next run carries on from the day after. Each day is
   fetched once, as the archive recorded it, so a nightly run only ever
   appends. The archive can re-record a past day during a repair; the file does
   not follow, and `--since`/`--until` into another `--out` fetches it again.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import re
import signal
import sys
import unicodedata
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from datetime import date as _date
from pathlib import Path
from typing import Any, NamedTuple, TextIO, Union, get_args, get_origin
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from themeparks import (
    APIError,
    BudgetExhaustedError,
    NetworkError,
    RateLimitError,
    ThemeParks,
    ThemeParksError,
)
from themeparks import TimeoutError as ApiTimeoutError  # noqa: A004 - the SDK's, not the builtin's
from themeparks._client import PACKAGE_VERSION, _default_user_agent
from themeparks._ergonomic.history import EntityRef, HistoryPage, HistorySpan
from themeparks._generated.models import HistoryDailyRow

# The command's identity IN FRONT OF the SDK's, not instead of it. It used to be
# the literal "themeparks-backfill/1": a hardcoded 1 that could never match a
# release, and it replaced the SDK's user agent entirely, so a support question
# about a bad download had no version to work from at either end.
USER_AGENT = f"themeparks-backfill/{PACKAGE_VERSION} {_default_user_agent()}"

EX_TEMPFAIL = 75

IDENTITY_COLUMNS = [
    # Identity first. A reader opening this in a spreadsheet should know what a
    # row is before they reach the numbers, and a table loaded from several files
    # needs parkId to tell them apart.
    "parkId",
    "parkName",
    "entityId",
    "entityName",
    "entityType",
]


def _flatten_columns(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Every scalar in a daily row, as one flat column name each.

    DERIVED FROM THE MODEL, not typed out. The hand-written list had drifted three
    ways at once: `unknownMinutes` and the whole `inParkHours` block were on every
    row the API returns and in no column, `extremeWaits` likewise, and
    `singleRider` carried two of its five percentiles while `standby` carried all
    five. Ten of thirty-six fields were missing, silently, from a file people pay
    for. Generated from the schema it cannot drift again: regenerate the models
    and the columns follow.
    """
    columns: list[str] = []
    for name, field in model.model_fields.items():
        inner = _stats_model(field.annotation)
        if inner is None:
            columns.append(
                f"{prefix}{name}" if prefix == "" else f"{prefix}{name[0].upper()}{name[1:]}"
            )
            continue
        head = name if prefix == "" else f"{prefix}{name[0].upper()}{name[1:]}"
        columns.extend(_flatten_columns(inner, head))
    return columns


#: Origins whose arguments are ELEMENTS, not nested blocks to flatten.
_COLLECTION_ORIGINS = (list, set, frozenset, tuple, dict)


def _stats_model(annotation: Any) -> type[BaseModel] | None:
    """The nested model an annotation wraps, or None for a scalar.

    Every nested block on a daily row is optional, so the annotation is a union
    with None and the model has to be dug out of it.
    """
    # A LIST OR DICT OF MODELS IS NOT A NESTED BLOCK. `get_args(list[Stats])` is
    # `(Stats,)`, so the model was found and the annotation treated as one object:
    # phantom columns in the header, then `AttributeError: type object 'list' has
    # no attribute 'model_fields'` on the first row. Any array-of-objects the API
    # adds would have done it.
    if get_origin(annotation) in _COLLECTION_ORIGINS:
        return None
    candidates = [annotation, *get_args(annotation)]
    for candidate in candidates:
        for unwrapped in (candidate, *get_args(candidate)):
            if isinstance(unwrapped, type) and issubclass(unwrapped, BaseModel):
                return unwrapped
    return None


DATA_COLUMNS = _flatten_columns(HistoryDailyRow)
CSV_COLUMNS = [*IDENTITY_COLUMNS, *DATA_COLUMNS]

# The `<outer><Inner>` rule is not injective: a top-level `standbyMin` alongside
# `standby.min` would produce one name twice, and `DictWriter` then writes the same
# value into both slots under a right-looking header. No collision exists in
# today's schema; this makes the next one a failure at import rather than a wrong
# number in a customer's file.
if len(set(CSV_COLUMNS)) != len(CSV_COLUMNS):
    _seen: set[str] = set()
    _dupes = sorted({c for c in CSV_COLUMNS if c in _seen or _seen.add(c)})  # type: ignore[func-returns-value]
    raise AssertionError(f"the daily row flattens to duplicate column names: {_dupes}")


def _cells(value: Any, prefix: str = "") -> dict[str, Any]:
    """One model flattened to `{column: value}`, mirroring `_flatten_columns`."""
    out: dict[str, Any] = {}
    for name, field in type(value).model_fields.items():
        column = f"{prefix}{name}" if prefix == "" else f"{prefix}{name[0].upper()}{name[1:]}"
        item = getattr(value, name, None)
        if _stats_model(field.annotation) is not None:
            if item is None:
                # An absent block is absent for a reason: no wait was in force, or
                # the park published no hours. Empty cells, never zeroes -- a zero
                # would read as "measured, and it was nothing".
                nested = _require(_stats_model(field.annotation))
                for column_name in _flatten_columns(nested, column):
                    out[column_name] = ""
            else:
                out.update(_cells(item, column))
            continue
        out[column] = _scalar(item)
    return out


def _require(model: type[BaseModel] | None) -> type[BaseModel]:
    if model is None:  # pragma: no cover - _cells only calls this when it is not
        raise AssertionError("expected a nested model")
    return model


#: Characters that make a spreadsheet treat a cell as a formula rather than text.
#: Tab and CR are included because Excel strips them and then reads what follows.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")

#: A number, strictly: no surrounding whitespace, no sign-only, no `\t5`. The
#: JavaScript SDK uses the same pattern. `float()` would call `"\t5"` numeric and
#: leave a tab-led cell undefended in one SDK and defended in the other, for a
#: file the two are supposed to write identically.
_NUMERIC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _defuse(text: str) -> str:
    """Prefix a cell that a spreadsheet would execute rather than display.

    A ride named `=cmd|...` is a formula to Excel. Every string that reaches a cell
    here comes from the API -- park and entity names -- and there is no path from a
    command-line argument into one, so this needs an upstream park feed to publish
    such a name. Cheap enough to do anyway.

    NUMERIC CELLS ARE LEFT ALONE, which is why this is not a bare startswith on the
    tuple: `-5` is a number and must stay one. Prefixing it would turn every
    negative value in the file into text and break arithmetic in the tool this
    exists to protect.
    """
    if not text.startswith(_FORMULA_LEADERS) or _NUMERIC.match(text):
        return text
    return "'" + text


def _scalar(value: Any) -> Any:
    """A cell a spreadsheet can read: dates and times as ISO, None as empty.

    UTC is written `Z`, not `+00:00`. Both are valid ISO 8601 and mean the same
    instant, but `Z` is what the API sends and what the JavaScript SDK's identical
    command writes, and a paid export of the same park should not differ by
    language. Python's `isoformat()` is the only reason it did.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, _date):
        return value.isoformat()
    unwrapped = getattr(value, "value", value)
    return _defuse(unwrapped) if isinstance(unwrapped, str) else unwrapped


def _csv_cell(value: Any) -> str:
    """One cell, quoted exactly as the JavaScript SDK quotes it.

    NOT `csv.DictWriter`, and the reason is a real defect. With
    `lineterminator="\n"`, the csv module's QUOTE_MINIMAL does not quote a bare
    carriage return on Python 3.9 or 3.10 -- it only quotes characters that appear
    in the line terminator -- so an entity name containing one produced a row that
    parsed as two, with every later column shifted. 3.11 changed the module to
    always quote CR and LF, so the bug was invisible on a modern interpreter and
    live on two supported ones.

    Relying on stdlib behaviour that moved between 3.10 and 3.11 cannot give a file
    that is byte-identical across Python versions, let alone identical to the
    JavaScript SDK's. Ten lines of explicit quoting can.
    """
    text = "" if value is None else str(value)
    if any(ch in text for ch in ('"', ",", "\r", "\n")):
        escaped = text.replace('"', '""')
        return f'"{escaped}"'
    return text


def _csv_line(cells: dict[str, Any]) -> str:
    """A row, in column order, LF-terminated.

    LF, not RFC 4180's CRLF: every reader accepts either, and this command also
    writes NDJSON with LF and has a JavaScript twin that writes LF, so one park
    should not come back as three different byte streams.
    """
    return ",".join(_csv_cell(cells.get(column)) for column in CSV_COLUMNS) + "\n"


def _csv_row(ref: EntityRef, row: Any, ident: _RowIdentity) -> dict[str, Any]:
    """One CSV row: the run's identity, then every field of the day's row."""
    return {
        "parkId": ident.park.id,
        "parkName": _defuse(ident.park.name),
        "entityId": ref.id,
        "entityName": _defuse(ref.name),
        "entityType": ref.entity_type,
        **_cells(row),
    }


class _Range(NamedTuple):
    """The days asked for with `--since` and `--until`, both inclusive, or None.

    None at either end means "as far as there is": back to where the plan
    reaches, forward to the newest final day.
    """

    since: str | None = None
    until: str | None = None


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
        if fmt == "csv" and write_header:
            # A UTF-8 BOM, so Excel on Windows does not read the file in the local
            # code page and render `Walt Disney World® Resort` as mojibake. The
            # primary reader of this file is a spreadsheet. Written with the header,
            # so a resumed file never gains a second.
            handle.write("\ufeff" + _csv_line(dict(zip(CSV_COLUMNS, CSV_COLUMNS))))

    def write(self, ref: EntityRef, row: Any) -> None:
        if self._fmt == "csv":
            self._handle.write(_csv_line(_csv_row(ref, row, self._ident)))
            return
        # Identity keys come FIRST in the object, so a human reading one line of
        # NDJSON sees what it is before the numbers.
        identity = {
            "parkId": self._ident.park.id,
            "parkName": self._ident.park.name,
            "entityId": ref.id,
            "entityName": ref.name,
            "entityType": ref.entity_type,
        }
        payload = {**identity, **row.model_dump(mode="json")}
        # THE ROW DOES NOT GET TO RENAME THE RUN. Models keep undeclared fields
        # now, so a row carrying its own `entityId` or `parkName` would win the
        # merge and silently relabel every line of a paid export. Re-asserting
        # after the merge costs nothing: a dict keeps the position of the FIRST
        # insertion, so identity still reads first.
        payload.update(identity)
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
# THE FORMAT IS IN THE FILENAME, not only inside the file. One state file served
# both formats, so finishing a park in ndjson, then csv, then ndjson again left
# the ndjson file with no state describing it -- `resuming` false, `has_rows`
# false, opened in append mode, every row duplicated, exit 0. That is verbatim
# the defect the state file was introduced to prevent.
STATE_SUFFIX = ".backfill-state.json"

#: Bumped when the meaning of a field changes. A state file from another version
#: is refused rather than guessed at, with one exception: version 1, below.
#:
#: 2: `end` is the newest FINAL day, and nothing after it is in the file. In
#: version 1 it was `retrievableThrough`, usually today, so the newest rows of a
#: finished file were partial days that no later run replaced.
STATE_VERSION = 2

#: How far back from a version-1 file's `end` its rows may be partial. The
#: archive records days 2 to 3 behind live data, so a 4.0 run that ended on its
#: `retrievableThrough` wrote two or three days that were not final. A week
#: covers that with room to spare, and every day re-fetched costs nothing more
#: than the one request its page already needs.
V1_UNSETTLED_DAYS = 7

SDK_NAME = "py"


def state_path_for(out_dir: Path, park_id: str, fmt: str) -> Path:
    """Where this park's state lives, for this format."""
    return out_dir / f"{park_id}.{fmt}{STATE_SUFFIX}"


def _columns_fingerprint(fmt: str) -> str:
    """A short hash of the exact header this build writes.

    THE HEADER IS PART OF THE RESUME CONTRACT and nothing recorded it. 3.3.0 wrote
    19 columns in a different order; 4.0.0 writes 41. `_decide` compared only the
    format, so a 3.3.0 state file resumed happily and 41-field rows were appended
    under a 19-column header: pandas refuses the file outright, and
    `csv.DictReader` silently reads standbyMin as changes. Exit 0 either way.

    Deriving the columns from the schema removed the reviewed diff that used to
    make a header change visible, so the fingerprint is what replaces it: any
    change to the column list, from any cause, makes every in-flight resume
    refuse instead of corrupt.
    """
    if fmt != "csv":
        return ""
    digest = hashlib.sha256("\n".join(CSV_COLUMNS).encode("utf-8")).hexdigest()
    return digest[:16]


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, **fields: Any) -> None:
    """Replace the state file in one step.

    Written beside it and renamed over it, so a crash or a full disk mid-write
    leaves the previous state rather than half of a new one. A torn state file
    reads as no state, and no state beside a file with rows is a refusal.
    """
    scratch = path.with_name(path.name + ".writing")
    try:
        scratch.write_text(json.dumps(fields, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def _state_mismatch(state: dict[str, Any], fmt: str) -> str | None:
    """Why this state file cannot be resumed by this build, or None.

    camelCase keys, deliberately: the JavaScript SDK writes the same file and the
    two used to differ only in `last_day` vs `lastDay` -- the two keys that matter
    on the interrupted path. Everything else was spelled identically, so the safe
    paths interoperated and nothing warned, while a Python run interrupted at 64
    rows and resumed by the JavaScript command produced 172 rows with 64
    duplicated keys and `complete: true`.
    """
    if state.get("sdk") != SDK_NAME:
        other = state.get("sdk")
        return f"it was written by the {other} SDK, and resuming across SDKs is not supported"
    if state.get("stateVersion") != STATE_VERSION:
        written = state.get("stateVersion")
        return f"it was written by a different version of this command (state v{written})"
    if state.get("format") != fmt:
        return f"it is a {state.get('format')} run"
    if state.get("columns") != _columns_fingerprint(fmt):
        return "the column layout changed since it was written"
    return None


class _StateFile(NamedTuple):
    """Where the state lives and the range it describes, fixed for one park."""

    path: Path
    fmt: str
    #: The first day the file was ASKED to start from: `--since`, or where the
    #: archive starts. Kept apart from the first day actually written, which the
    #: key's window can push later, so the same `--since` keeps working.
    since: Day
    end: Day


class _Checkpoint(NamedTuple):
    """Where a run has got to, as the state file records it."""

    #: The first day actually written to the file. On a first run the key's
    #: window can push it later than `since`.
    start: Day
    last_day: date | str | None
    resume_from: str | None
    #: Bytes of the data file this state vouches for. A rerun first cuts the
    #: file back to exactly this, so nothing written after the checkpoint (a
    #: half page when the run was killed) can ever be appended twice.
    size: int
    complete: bool


def _record(sf: _StateFile, cp: _Checkpoint) -> None:
    """Write the state file. `complete` is the fact the old checkpoint could not express.

    `cp.start` is the ORIGINAL first day of the file, not the day a resumed run
    happened to begin at. The two call sites used to disagree about that, so a
    run interrupted twice recorded the second resume point as though it were the
    beginning and lost the real range.
    """
    last = cp.last_day
    _write_state(
        sf.path,
        sdk=SDK_NAME,
        sdkVersion=PACKAGE_VERSION,
        stateVersion=STATE_VERSION,
        format=sf.fmt,
        columns=_columns_fingerprint(sf.fmt),
        # None, never the string "None". `str(None)` put the literal "None" in
        # the file where the JavaScript SDK writes null, and "None" is truthy.
        start=_day_str(cp.start),
        since=_day_str(sf.since),
        end=_day_str(sf.end),
        lastDay=last.isoformat() if isinstance(last, date) else last,
        resumeFrom=cp.resume_from,
        size=cp.size,
        complete=cp.complete,
    )


def _day_str(value: Day) -> str | None:
    """A day as a string, or None. Never the string "None"."""
    return None if value is None else str(value)


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


def _next_page_start(next_url: str | None) -> str | None:
    """The day a paged history URL starts on, or None if it does not say.

    The SDK follows the server's `next` verbatim; all that is wanted here is its
    `from`, to write into the state file as a bare day. A day keeps the state
    readable and sends a resumed run down the same code path as a first run.
    """
    if not next_url:
        return None
    query = parse_qs(urlparse(next_url).query)
    values = query.get("from") or []
    return values[0] if values and values[0] else None


class _Plan(NamedTuple):
    """What a run should do about a park, once its state file has been read."""

    start: Day
    has_rows: bool
    #: The first day already in the file, or None on a first run.
    prior_start: str | None
    #: True when rows from an EARLIER run are already in the file. Every deletion
    #: in this module has to consult it: `written == 0` means "this process wrote
    #: nothing", which on a resumed run is not the same as "the file is empty".
    resumed: bool = False
    #: True when a FINISHED file is being carried forward to new final days.
    extending: bool = False
    #: The first day the file was asked to start from. See `_StateFile.since`.
    since: Day = None
    #: The newest day already in the file, carried into the next checkpoint.
    prior_last_day: str | None = None


def _next_day(value: Day) -> str:
    """The day after `value`, as YYYY-MM-DD."""
    return (date.fromisoformat(str(value)) + timedelta(days=1)).isoformat()


def _days_before(value: Day, days: int) -> str:
    return (date.fromisoformat(str(value)) - timedelta(days=days)).isoformat()


def _later(a: Day, b: Day) -> Day:
    """The later of two days, either of which may be None. ISO days sort as text."""
    if a is None:
        return b
    if b is None:
        return a
    return a if str(a) >= str(b) else b


def _refuse(out_path: Path, why: str) -> int:
    print(
        f"  {out_path.name}: {why}.\n"
        f"    --overwrite   replace it with the range asked for\n"
        f"    or pass a different --out and run again",
        file=sys.stderr,
    )
    return 1


def _range_fits(
    out_path: Path, state: dict[str, Any], rng: _Range, archive_from: Day, continue_at: str
) -> int | None:
    """None when `--since`/`--until` agree with the file being continued, else an exit code.

    A file here is one contiguous range of days, and a run can only append to it,
    so two requests cannot be honoured without rewriting it: a `--since` before
    the file's first day, and one after the day it would continue from, which
    would leave a gap the state file could not describe. Both are refused rather
    than quietly ignored, which would hand back a file that is not what was asked
    for.

    THE SAME `--since` IS ALWAYS ACCEPTED, even when it is before the file's first
    day. On a plan short of the full archive, `--since 2025-01-01` starts the
    file at the first day the key can read, and a cron line repeating it every
    night must keep working. So a `--since` is judged against the first day
    WRITTEN, except that the one the file was asked to start from (`since` in the
    state) is always fine. A `--since` inside the file is fine and common too: one
    computed as "30 days ago" moves forward every night. Before the archive
    starts is the same as its start.
    """
    file_start = state.get("start")
    asked = state.get("since") or file_start
    since = _later(rng.since, archive_from) if rng.since is not None else None
    if (
        since is not None
        and file_start is not None
        and str(since) < str(file_start)
        and str(since) != str(asked)
    ):
        return _refuse(
            out_path,
            f"it was started from {file_start}, and --since {rng.since} would need days "
            f"before that. Appending cannot add them",
        )
    if rng.until is not None and file_start is not None and rng.until < str(file_start):
        return _refuse(out_path, f"it was started from {file_start}, after --until {rng.until}")
    if since is not None and str(since) > continue_at:
        return _refuse(
            out_path,
            f"it continues from {continue_at}, so starting at --since {rng.since} would "
            f"leave a gap",
        )
    return None


class _Ask(NamedTuple):
    """The range a run is asked for: where the archive starts, the newest day
    to fetch, and `--since`/`--until`."""

    archive_from: Day
    end: Day
    rng: _Range


def _decide(out_path: Path, state_path: Path, fmt: str, overwrite: bool, ask: _Ask) -> _Plan | int:
    """A `_Plan` to proceed with, or an exit code meaning "do not".

    Split out of `backfill_park` because it got long enough for ruff to object,
    and ruff was right: deciding whether to write is a different job from
    writing. Every branch here exists for a defect measured in review -- see the
    STATE block above for the five of them.
    """
    if ask.end is None:
        # Nothing is final: a park the archive has not recorded a day of yet.
        # Nothing is touched, so an existing file and its state stay as they are.
        print(
            f"  nothing final to fetch into {out_path.name} yet. The archive records "
            f"days 2 to 3 behind live data; run again later",
            file=sys.stderr,
        )
        return 0

    state = {} if overwrite else _read_state(state_path)
    if overwrite:
        out_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)

    file_exists = out_path.exists() and out_path.stat().st_size > 0

    # A state file describing a data file that is no longer there. Continuing
    # would write a file that starts part-way through its range and then record
    # it as complete. There is nothing to continue, so start again.
    if state and not file_exists:
        state = {}

    if state and _upgradable(state, fmt):
        state = _upgrade_v1(state, out_path, state_path, fmt)

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

    # A state file this build cannot continue. Refusing is the only safe answer:
    # the file beside it was written to a different contract, and appending to it
    # produces a file no reader can parse -- or worse, one that parses wrongly.
    #
    # FINISHED OR NOT. Only an unfinished one used to be refused: a finished one
    # fell through to a fresh start, and a fresh start opens the existing file in
    # append mode, so the whole archive went in a second time under a second
    # header, exit 0. A finished file is continued now, so it is refused too.
    mismatch = _state_mismatch(state, fmt) if state else None
    if mismatch is not None:
        kind = "a finished" if state.get("complete") else "an unfinished"
        print(
            f"  there is {kind} {out_path.name} beside this state file, but "
            f"{mismatch}.\n"
            f"    --overwrite   start this park again from the beginning\n"
            f"    or move both files aside and run again",
            file=sys.stderr,
        )
        return 1

    if state:
        checked = _back_to_checkpoint(out_path, state_path, state)
        if isinstance(checked, int):
            return checked
        state = checked
    if not state:
        return _first_run(out_path, ask)
    return _continue(out_path, state, ask)


def _back_to_checkpoint(
    out_path: Path, state_path: Path, state: dict[str, Any]
) -> dict[str, Any] | int:
    """Cut the file back to what the state vouches for: the checkpoint's `size`.

    THIS IS WHAT MAKES A RERUN IDEMPOTENT. The state used to be written only at
    the end of a run or on an error the SDK raised, so Ctrl-C, SIGTERM or
    SIGKILL during a nightly extension left `complete: true` with the old end,
    and the rerun appended the same days again: 70 duplicate rows, exit 0. The
    state is now written after every page with the file's size at that moment,
    so whatever is past that size was written after the last checkpoint -- half a
    page, or a torn line -- and is discarded here, then fetched again.

    A file SHORTER than its checkpoint was changed by something else, and
    appending would leave a hole the state claims is filled, so it is refused.

    A state with no `size` predates it. Its file is cut back by date instead, to
    the day it continues from, which reads the file once and then records a size.
    Returns the state to go on with; an empty dict when nothing is left in the
    file, which is a first run.
    """
    actual = out_path.stat().st_size
    size = state.get("size")
    if not isinstance(size, int):
        keep_through = _legacy_keep_through(state)
        fmt = str(state.get("format"))
        if keep_through is not None and _trim_after(out_path, fmt, keep_through) == 0:
            out_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
            return {}
        if keep_through is not None and not state.get("complete"):
            state = {**state, "resumeFrom": _next_day(keep_through)}
        state = {**state, "size": out_path.stat().st_size}
        _write_state(state_path, **state)
        return state
    if actual < size:
        return _refuse(
            out_path,
            f"it is {actual} bytes, shorter than the {size} its state file records, so "
            f"something other than this command changed it",
        )
    if actual > size:
        print(
            f"  discarding the last {actual - size} bytes of {out_path.name}: written "
            f"after the last checkpoint, and fetched again now",
            file=sys.stderr,
        )
        with out_path.open("r+b") as handle:
            handle.truncate(size)
    if size == 0:
        # Nothing survived the cut: this is a first run, from the range asked.
        state_path.unlink(missing_ok=True)
        return {}
    return state


def _legacy_keep_through(state: dict[str, Any]) -> str | None:
    """For a state without `size`: the newest day its file can be trusted to hold.

    A finished file holds every day through `end`. An unfinished one holds every
    day before `resumeFrom`, the page boundary; rows from that day on were
    written after it, by a run that was then killed. Without a boundary there is
    only `lastDay`, the newest day ANY entity reached -- and rows arrive entity by
    entity, so another entity may have stopped days earlier. The page that run
    was on began at most 30 days before `lastDay` (a page is up to 31 days), so
    that is where it is safe to go back to. Resuming at `lastDay` itself, as
    before, lost the later entities' days for good.
    """
    if state.get("complete"):
        end = state.get("end")
        return str(end) if end else None
    resume = state.get("resumeFrom")
    if resume:
        return _days_before(resume, 1)
    last_day = state.get("lastDay")
    start = state.get("start")
    if last_day:
        # No earlier than the file's own first day: nothing before it exists.
        back = _days_before(last_day, PAGE_DAYS)
        return back if not start or back >= _days_before(start, 1) else _days_before(start, 1)
    return _days_before(start, 1) if start else None


#: The most park-local days one page of the park daily endpoint covers.
PAGE_DAYS = 31


def _first_run(out_path: Path, ask: _Ask) -> _Plan | int:
    """A park with no file yet: from `--since`, or wherever the archive starts."""
    rng = ask.rng
    start = _later(rng.since, ask.archive_from) if rng.since is not None else ask.archive_from
    if rng.since is not None and str(start) > str(ask.end):
        print(
            f"  nothing to fetch into {out_path.name}: --since {rng.since} is after "
            f"{ask.end}, the newest final day",
            file=sys.stderr,
        )
        return 0
    return _Plan(start=start, has_rows=False, prior_start=None, since=start)


def _continue(out_path: Path, state: dict[str, Any], ask: _Ask) -> _Plan | int:
    """A file this command wrote: carry it forward, or say why not."""
    rng, archive_from, end = ask.rng, ask.archive_from, ask.end
    carried = _Plan(
        start=None,
        has_rows=True,
        prior_start=state.get("start"),
        resumed=True,
        since=state.get("since") or state.get("start"),
        prior_last_day=state.get("lastDay"),
    )
    if state.get("complete"):
        # FINISHED IS NOT FOREVER. It used to be: a rerun printed "already
        # complete" and exited 0 without asking for a single new day, so a
        # nightly cron looked healthy and never updated. The file holds every
        # day through `end`, so the next day is where it carries on.
        recorded_end = state.get("end")
        continue_at = _next_day(recorded_end) if recorded_end else str(state.get("start"))
        refused = _range_fits(out_path, state, rng, archive_from, continue_at)
        if refused is not None:
            return refused
        if end is None or continue_at > str(end):
            print(
                f"  up to date: {out_path.name} is complete through {recorded_end}, "
                f"and there is no final day after it yet",
                file=sys.stderr,
            )
            return 0
        return carried._replace(start=continue_at, extending=True)

    # THE PAGE BOUNDARY, not the newest row. `lastDay` is the highest date
    # written; the page it came from covered further, because an entity that
    # stopped reporting has no rows for the tail days. Resuming at `last_day`
    # re-fetches a day already in the file and appends every row of it again.
    # Every state this build writes has a boundary; `_back_to_checkpoint` gives
    # one to an older state that did not.
    resume_at = state.get("resumeFrom") or state.get("lastDay")
    continue_at = str(resume_at or state.get("start") or archive_from)
    refused = _range_fits(out_path, state, rng, archive_from, continue_at)
    if refused is not None:
        return refused
    if rng.until is not None and continue_at > str(end):
        print(
            f"  nothing to add: {out_path.name} continues from {continue_at}, after "
            f"--until {rng.until}",
            file=sys.stderr,
        )
        return 0
    return carried._replace(start=continue_at)


# --------------------------------------------------------------------------
# Files written by 4.0.x, whose newest rows may be partial days.
# --------------------------------------------------------------------------


def _upgradable(state: dict[str, Any], fmt: str) -> bool:
    """A version-1 state file from this SDK, for this format and column layout."""
    return (
        state.get("stateVersion") == 1
        and state.get("sdk") == SDK_NAME
        and state.get("format") == fmt
        and state.get("columns") == _columns_fingerprint(fmt)
    )


def _upgrade_v1(
    state: dict[str, Any], out_path: Path, state_path: Path, fmt: str
) -> dict[str, Any]:
    """Make a 4.0 file one this build can continue, replacing its unsettled tail.

    4.0 ended every run at `retrievableThrough`, usually today, so the last few
    days of a finished 4.0 file were written while they were still changing:
    Magic Kingdom's last day summed to about half the operating minutes of a
    full one. Nothing ever replaced them, because a finished park was never
    fetched again.

    Which of those days were final at the time was not recorded, so every row
    dated within `V1_UNSETTLED_DAYS` of that run's end is removed and the state
    is set to carry on from the day after the cut. The next request then fetches
    those days again, final this time. A file whose newest row is already older
    than the cut, a park that stopped reporting long ago, is not read at all.

    A file that lies WHOLLY inside the cut, as every anonymous 7-day file does,
    is downloaded again instead: the cut leaves it empty, its checkpoint says 0
    bytes, and `_back_to_checkpoint` treats that as a first run. Continued, it
    would carry on from a day the key may no longer read, which a continued file
    is not allowed to skip past.

    The new state is written straight away, so a run that fails after this
    point does not trim the same file twice.
    """
    end = state.get("end")
    upgraded = {**state, "stateVersion": STATE_VERSION}
    if not end:
        return upgraded
    keep_through = _days_before(end, V1_UNSETTLED_DAYS)
    last_day = state.get("lastDay")
    trimmed = last_day is None or str(last_day) > keep_through
    if trimmed:
        _trim_after(out_path, fmt, keep_through)
        upgraded["lastDay"] = keep_through if last_day is not None else None
    if state.get("complete"):
        upgraded["end"] = keep_through
    else:
        resume = state.get("resumeFrom") or last_day
        if resume is None or str(resume) > _next_day(keep_through):
            upgraded["resumeFrom"] = _next_day(keep_through)
    if trimmed or state.get("complete"):
        # Nothing past the cut is left, so the whole file is vouched for.
        upgraded["size"] = out_path.stat().st_size
    _write_state(state_path, **upgraded)
    return upgraded


def _trim_after(out_path: Path, fmt: str, keep_through: str) -> int:
    """Remove every row dated after `keep_through`, leaving the rest byte for byte.

    Returns how many rows were kept, counting any it could not read.

    Streamed into a file beside the original and swapped in with one rename, so
    an interruption leaves either the old file or the new one, never half of
    each, and the half-written copy is removed. A line or record that cannot be
    read is kept: this command does not get to decide that something it does not
    understand is worthless.

    The CSV is parsed and written back with this module's own quoting, which is
    a function of the text alone, so a kept record comes out as it went in.
    """
    scratch = out_path.with_name(out_path.name + ".trimming")
    try:
        with out_path.open(encoding="utf-8", newline="") as src:
            kept = _copy_rows_through(src, scratch, fmt, keep_through)
        os.replace(scratch, out_path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise
    return kept


def _copy_rows_through(src: TextIO, scratch: Path, fmt: str, keep_through: str) -> int:
    """The body of `_trim_after`: copy every row dated on or before `keep_through`."""
    kept = 0
    with scratch.open("w", encoding="utf-8", newline="") as dst:
        if fmt == "csv":
            reader = csv.reader(src)
            header = next(reader, None)
            if header is not None:
                dst.write(",".join(_csv_cell(cell) for cell in header) + "\n")
                names = [cell.lstrip("\ufeff") for cell in header]
                # No `date` column: not a file this can read, so it is copied whole.
                column = names.index("date") if "date" in names else -1
                for record in reader:
                    if 0 <= column < len(record) and record[column] > keep_through:
                        continue
                    kept += 1
                    dst.write(",".join(_csv_cell(cell) for cell in record) + "\n")
        else:
            for line in src:
                try:
                    day = json.loads(line).get("date")
                except (ValueError, AttributeError):
                    day = None
                if isinstance(day, str) and day > keep_through:
                    continue
                kept += 1
                dst.write(line)
    return kept


class _Job(NamedTuple):
    """Everything streaming one park needs, so the streamer takes two arguments."""

    history: Any
    out_path: Path
    fmt: str
    end: Day
    has_rows: bool
    ident: _RowIdentity
    #: Where every checkpoint is written.
    state: _StateFile
    #: True when the file is being CONTINUED, so its next day is fixed.
    resumed: bool = False


class _Progress:
    """How far the stream got. MUTABLE, and that is the point.

    `_stream` used to return this as a tuple, which meant an exception threw the
    numbers away: the budget handler then had nothing to record and read the state
    file back instead, which on a first run does not exist. The resume point was
    lost on exactly the interruption it exists for, and a re-run started over.

    Owned by the caller, updated in place, so it is readable after a raise.
    """

    def __init__(self, file_start: Day = None, last_day: str | None = None) -> None:
        self.written = 0
        self.last_day: date | str | None = last_day
        self.resume_from: str | None = None
        self.skipped = False
        #: The key cannot read the day a continued file carries on from.
        self.out_of_reach = False
        #: The first day in the file: the prior run's on a continued file, the
        #: first day this run actually requested successfully on a new one.
        self.file_start: Day = file_start

    def checkpoint(self, sf: _StateFile, size: int, resume_from: str | None) -> None:
        """Record that the first `size` bytes of the file are good through here."""
        _record(sf, _Checkpoint(self.file_start, self.last_day, resume_from, size, False))


def _size(handle: TextIO) -> int:
    """Bytes written so far, after pushing Python's buffer to the OS."""
    handle.flush()
    return os.fstat(handle.fileno()).st_size


def _stream(job: _Job, start: Day, progress: _Progress) -> None:
    """Write the range to the file, recovering once from a window 403.

    Separated from `backfill_park` because that function was deciding, printing,
    streaming and recording in one place, and ruff counted the statements before
    a reader had to. This is the streaming.

    THE STATE IS WRITTEN BEFORE THE FIRST REQUEST AND AFTER EVERY PAGE, with the
    file's size at that moment. Nothing else has to run for it to be right: a
    run stopped by Ctrl-C, SIGTERM or SIGKILL leaves a state that names the last
    page boundary, and the rerun cuts the file back to it. An extending run
    marks the file unfinished before it appends its first row, so no rerun can
    mistake a half-extended file for a finished one.
    """
    handle: TextIO

    def note_page(page: HistoryPage) -> None:
        """Checkpoint, called once every row of a page is written.

        The day the NEXT page starts on, taken from the server's own `next` URL,
        so a resumed run asks for nothing twice. None on the last page, where
        there is nothing left to carry on from; completion is recorded by the
        caller once the file is closed.
        """
        progress.resume_from = _next_page_start(page.next_url)
        if progress.resume_from is not None:
            progress.checkpoint(job.state, _size(handle), progress.resume_from)

    def write_rows(writer: Writer, first_day: Day) -> None:
        """Stream one range into the file. Raises whatever the SDK raises."""
        for ref, row in job.history.days_with_entities(first_day, job.end, on_page=note_page):
            if progress.written == 0 and progress.file_start is None:
                # The first row of a new file: this range was not refused, so its
                # first day is the file's, whatever `--since` asked for.
                progress.file_start = first_day
            writer.write(ref, row)
            progress.written += 1
            # MAX, not last-seen. `_daily_rows` walks entities and then each
            # entity's days, so the final row belongs to the alphabetically last
            # entity, which may have stopped reporting mid-page.
            previous = progress.last_day
            progress.last_day = (
                row.date if previous is None else max(date.fromisoformat(str(previous)), row.date)
            )
            if progress.written % 5000 == 0:
                print(f"  {progress.written} rows, at {progress.last_day}", file=sys.stderr)

    with job.out_path.open("a", newline="", encoding="utf-8") as handle:
        # Before the first request: from here on the file may grow, so the state
        # must say where it was good up to.
        progress.checkpoint(job.state, _size(handle), None if start is None else str(start))
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
            # A FILE BEING CONTINUED CANNOT JUMP FORWARD. Starting at the key's
            # first day instead of the day the file continues from leaves a gap
            # the state file cannot describe, so the file would claim days it
            # does not hold. It happens when a cron has not run for longer than
            # the key's window, or the key lost its plan. Refused, file untouched.
            if job.resumed:
                if start is None or floor <= str(start):
                    raise
                print(
                    f"  this key reaches back to {floor}, but {job.out_path.name} "
                    f"continues from {start}: the days between are out of reach, and "
                    f"carrying on from {floor} would leave a gap in the file. The rows "
                    f"already downloaded are left alone.\n"
                    f"    --overwrite   start the file again from what this key can read\n"
                    f"    or pass a different --out and run again",
                    file=sys.stderr,
                )
                progress.out_of_reach = True
                return
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


def _window_closed(out_path: Path, end: Day, start: Day, *, resumed: bool) -> int:
    """This key's window does not reach the day this park would start at.

    NEVER DELETE ROWS AN EARLIER RUN DOWNLOADED. `start` is the resume point on a
    rerun, so a key rotated out of a scheduler's environment or a lapsed
    subscription used to wipe the partial archive and exit 0 -- the scheduler
    logged success -- and then trap: the file gone, the state surviving,
    `has_rows` false, and every later run re-entering this branch and exiting 0
    with no data.
    """
    _say_empty(end, start)
    if resumed:
        print(
            "  the rows already downloaded are left alone. Your plan no longer "
            "reaches the day this run would continue from",
            file=sys.stderr,
        )
        return 1
    out_path.unlink(missing_ok=True)
    return 0


def _nothing_written(
    out_path: Path, state_path: Path, progress: _Progress, *, resumed: bool
) -> int:
    """The park had nothing in this key's window. Tidy up, or refuse to.

    On a first run both files go: an empty file reads as "this park has no
    history". On a RESUMED run an earlier run's rows are real and are not ours to
    remove, so the state is kept and the exit code says the run did not finish.

    A continued file the key can no longer reach the next day of is refused
    outright: nothing written and the state untouched, so the next run meets the
    same refusal until someone decides, rather than carrying on with a gap.
    """
    if progress.out_of_reach or resumed:
        # The checkpoint written before the first request already says where
        # the file continues from.
        return 1
    out_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return 0


@contextlib.contextmanager
def _park_lock(out_dir: Path, park_id: str, fmt: str) -> Iterator[bool]:
    """Hold an advisory lock on one park's output, yielding whether it was got.

    Two runs on the same park and `--out`, say a cron overlapping a manual run,
    would each read the same state and append the same days. The lock is a
    separate file because the state and data files are replaced by rename, and a
    lock on a replaced file protects nothing. The operating system releases it
    when the process ends, however it ends, so a killed run never leaves a stale
    lock behind. Where `fcntl` does not exist (Windows) this does not lock.
    """
    try:
        import fcntl  # noqa: PLC0415 - POSIX only; absent on Windows
    except ImportError:  # pragma: no cover - exercised on Windows only
        yield True
        return
    path = out_dir / f".{park_id}.{fmt}.backfill-lock"
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_end(span: HistorySpan, rng: _Range) -> Day:
    """The last day this run asks for: the newest FINAL day, or `--until` if earlier.

    Not `retrievable_through`. That is usually today, and today's row is the day
    so far; the archive records days 2 to 3 behind, so the days in between can
    still change too. Ending there wrote partial rows -- Magic Kingdom's last day
    at about half a full day's operating minutes -- and, since a finished park
    was never fetched again, they stayed partial.
    """
    end: Day = span.final_through
    if rng.until is not None and end is not None and rng.until < str(end):
        end = rng.until
    return end


def _announce(park_id: str, plan: _Plan, ask: _Ask, span: HistorySpan, out_path: Path) -> None:
    """Say what this run will fetch, and why it stops where it does."""
    note = " (new days)" if plan.extending else " (resumed)" if plan.resumed else ""
    print(f"{park_id}: {plan.start} .. {ask.end}{note} -> {out_path}", file=sys.stderr)
    through = span.retrievable_through
    if through is not None and str(through) > str(ask.end) and ask.end == span.final_through:
        print(
            f"  stopping at {ask.end}: the days after it are still being recorded and "
            f"can change. The next run adds them once they are final",
            file=sys.stderr,
        )


def backfill_park(  # noqa: PLR0913 - window is keyword-only, added without breaking callers
    tp: ThemeParks,
    park: _Park,
    out_dir: Path,
    fmt: str,
    overwrite: bool = False,
    *,
    window: _Range | None = None,
) -> int:
    """Write one park's daily history. Returns 0, or EX_TEMPFAIL if the budget ran out.

    `window` is `--since`/`--until`. Without it the range is everything the key
    may read, through the newest final day.
    """
    with _park_lock(out_dir, park.id, fmt) as held:
        if not held:
            print(
                f"{park.id}: another themeparks-backfill is writing this park into "
                f"{out_dir} right now. Two at once would each append the same days; "
                f"wait for it to finish",
                file=sys.stderr,
            )
            return 1
        return _backfill_park(tp, park, out_dir, fmt, overwrite=overwrite, rng=window or _Range())


def _backfill_park(  # noqa: PLR0913 - the arguments of backfill_park, resolved
    tp: ThemeParks, park: _Park, out_dir: Path, fmt: str, *, overwrite: bool, rng: _Range
) -> int:
    """`backfill_park`, once this process holds the park's lock."""
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
    state_path = state_path_for(out_dir, park_id, fmt)
    ask = _Ask(span.archive_from, _run_end(span, rng), rng)
    decided = _decide(out_path, state_path, fmt, overwrite, ask)
    if isinstance(decided, int):
        return decided
    start, has_rows, prior_start, resumed_run = decided[:4]
    end = ask.end
    sf = _StateFile(state_path, fmt, decided.since, end)
    _announce(park_id, decided, ask, span, out_path)

    if _is_empty_window(start, end):
        return _window_closed(out_path, end, start, resumed=resumed_run)

    ident = _RowIdentity(park)
    job = _Job(history, out_path, fmt, end, has_rows, ident, sf, resumed=resumed_run)
    progress = _Progress(prior_start, decided.prior_last_day)
    try:
        _stream(job, start, progress)
    except BudgetExhaustedError as exc:
        # The budget is hourly, so a spent one can be most of an hour from
        # resetting. Exit 75 rather than sleeping; the last checkpoint already
        # says where to carry on.
        if progress.written == 0 and not resumed_run:
            # A budget spent before the first page left a 0-byte file that reads
            # as "this park has no history".
            out_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
        wait = exc.retry_after or 0
        print(
            f"  budget spent; rerun the same command in {wait / 60:.0f} min to continue",
            file=sys.stderr,
        )
        return EX_TEMPFAIL
    # ORDER MATTERS AND IT BIT ONCE: BudgetExhaustedError subclasses
    # RateLimitError, so this clause above the budget one catches it first and
    # turns exit 75 into a traceback and exit 1 -- the precise regression the
    # budget handler exists to prevent.
    except (ThemeParksError, OSError):
        # The last checkpoint already says where to carry on, and the rerun cuts
        # off anything written after it. AN EMPTY FILE IS A LIE: opening the file
        # created it before the first request, so a park that failed with nothing
        # written left a 0-byte file that reads as "this park has no history" --
        # on a six-park destination the customer counts six files and never sees
        # which one is empty.
        #
        # `written` counts rows THIS process wrote, so on a resumed run it is 0
        # while the file holds everything the previous runs fetched. Deleting it
        # there destroyed the archive and left the state file pointing into the
        # middle of it, so the next run appended only the tail and recorded
        # `complete: true`.
        if progress.written == 0 and not resumed_run:
            out_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
        raise

    if progress.out_of_reach or (progress.skipped and progress.written == 0):
        return _nothing_written(out_path, state_path, progress, resumed=resumed_run)

    # Completion is RECORDED, never inferred from a missing file. That is the
    # distinction the old checkpoint could not make.
    size = out_path.stat().st_size
    _record(sf, _Checkpoint(progress.file_start, progress.last_day, None, size, True))
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

    # WHEN THE EXACT NAME IS AMBIGUOUS, the exact matches ARE the candidates.
    # "Disneyland Park" is two live parks, Anaheim and Paris; widening to
    # substrings adds Hong Kong Disneyland Park, which is not what was typed and
    # pads the one list whose whole job is "which of these did you mean".
    park_hits = (
        exact_park
        if len(exact_park) > 1
        else [(pid, pname) for pid, pname, _, _ in catalogue if lowered in _normalize(pname)]
    )
    dest_hits = {did: dname for _, _, did, dname in catalogue if lowered in _normalize(dname)}
    if len(dest_hits) == 1 and not park_hits:
        return parks_in(next(iter(dest_hits)))

    # A UNIQUE SUBSTRING RESOLVES. `themeparks-backfill "magic kingdom"` names
    # exactly one park, and refusing a query that is unambiguous is hostile.
    if len(park_hits) == 1:
        return park_hits

    if not park_hits and not dest_hits:
        raise SystemExit(
            f'no park or destination matching "{wanted}".\n'
            f"  themeparks-backfill --list           everything\n"
            f'  themeparks-backfill --list disney    the ones matching "disney"'
        )

    # AMBIGUOUS: list the ids and stop. THE LABEL IS NOT THE NAME -- this used to
    # `return candidates`, whose second element is the formatted display label, so
    # a one-hit substring downloaded the right park and wrote
    # `"Magic Kingdom Park  (Walt Disney World® Resort)"` into the parkName column
    # of every one of ~73,000 rows, and echoed the destination twice. Four live
    # names reach this path. Shipped in 3.3.0.
    #
    # The destination is load-bearing in the LABEL: two live parks are named
    # exactly "Disneyland Park" -- Anaheim and Paris -- so bare names would offer
    # a choice between two identical lines. Sorted by park name, id first, so the
    # id is the copy-pasteable part.
    dest_of = {pid: dname for pid, _, _, dname in catalogue}
    labelled = (
        [(pid, pname, f"{pname}  ({dest_of[pid]})") for pid, pname in park_hits]
        if park_hits
        else [
            (did, dname, f"{dname}  (destination, {len(parks_in(did))} parks)")
            for did, dname in dest_hits.items()
        ]
    )
    lines = "\n".join(
        f"  {cid}  {label}" for cid, _, label in sorted(labelled, key=lambda c: (c[1], c[0]))
    )
    raise SystemExit(
        f'"{wanted}" matches {len(labelled)}. Pass one of these ids, or the '
        f"destination name to get all of its parks:\n{lines}"
    )


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


def _print_list(catalogue: list[tuple[str, str, str, str]], needle: str | None) -> int:
    """Parks grouped under their destination, so a destination id is visible too."""
    shown = catalogue
    if needle:
        lowered = _normalize(needle)
        shown = [c for c in catalogue if lowered in _normalize(c[1]) or lowered in _normalize(c[3])]
    if not shown:
        print(f'nothing matching "{needle}"', file=sys.stderr)
        return 1

    by_dest: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for pid, pname, did, dname in shown:
        by_dest.setdefault((did, dname), []).append((pid, pname))
    for (did, dname), parks in sorted(by_dest.items(), key=lambda kv: kv[0][1]):
        # THE TOTAL, counted from the UNFILTERED catalogue. Counting the filtered
        # rows made `--list epcot` print "all 1 parks" for a destination with
        # six, on the one line whose entire job is that number -- and that line
        # is an instruction to pass the destination id, so the number is what the
        # reader decides on.
        total = sum(1 for c in catalogue if c[2] == did)
        note = "" if total == len(parks) else f" ({len(parks)} shown)"
        word = "park" if total == 1 else "parks"
        print(f"{did}  {dname}  <- destination: all {total} {word}{note}")
        for pid, pname in sorted(parks, key=lambda p: p[1]):
            print(f"    {pid}  {pname}")
    return 0


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_day(value: str) -> str:
    """An argparse type: a real calendar day written YYYY-MM-DD, returned as given.

    Strict on purpose. From 3.11 `date.fromisoformat` also takes `20250101` and
    week dates, and 3.9 does not, so the same command line would mean something
    on one interpreter and fail on another. Only the form the API itself uses is
    accepted, everywhere.
    """
    try:
        if _ISO_DAY.match(value):
            return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"expected a day as YYYY-MM-DD, got {value!r}")


EPILOG = """examples:
  export THEMEPARKS_API_KEY=tpw_your_key
      how far back this reaches is your plan, so without a key you get the 7
      days anonymous access allows -- and the run still succeeds, quietly.

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

  themeparks-backfill "Epcot" --since 2025-01-01
      from a day of your choosing instead of as far back as your plan reaches.
      --until YYYY-MM-DD sets the last day. Both are inclusive.

  themeparks-backfill "Epcot"     (again, from cron, every night)
      adds the days that became final since the last run, and nothing else.

only final days are written. Today's row is the day so far, and the archive
records days 2 to 3 behind live data, so the newest days can still change. A run
ends at the newest final day and the next run carries on from the day after, so
the file only ever grows. Each day is fetched once, as the archive recorded it;
if the archive later re-records past days (a repaired feed), fetch them again
with --since/--until into a different --out, or start again with --overwrite.

stopping is safe at any point: the state is saved after every page, and the next
run cuts off anything written after it, so no day is appended twice.

--since applies when a file is started. A later run continues that file forward
and accepts the same --since, or a later one. One earlier than the file's first
day, or past the day it continues from, is refused: use --overwrite, or a
different --out. A --since before your plan's window starts the file at the
first day your key can read, and the same --since keeps working on every later
run.

a file is never continued past a gap. If the day it continues from is older than
your key can read (a cron that missed more days than your window, or a plan that
lapsed), the run is refused and the file left alone.

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
        " that finished is brought up to date rather than fetched twice, and a"
        " file this command did not write is never touched.",
    )
    parser.add_argument(
        "--since",
        type=_iso_day,
        metavar="YYYY-MM-DD",
        help="first day to download, inclusive (default: as far back as your plan reaches)",
    )
    parser.add_argument(
        "--until",
        type=_iso_day,
        metavar="YYYY-MM-DD",
        help="last day to download, inclusive (default: the newest final day)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"themeparks-backfill {PACKAGE_VERSION}",
        help="print the version and exit",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="output directory (default: .)",
    )
    args = parser.parse_args(argv)
    if args.since is not None and args.until is not None and args.since > args.until:
        parser.error(f"--since {args.since} is after --until {args.until}")

    _use_utf8(sys.stdout, sys.stderr)

    # --list first: it is how you find a park, so it must work before you have a
    # key and before you have decided to pay for anything.
    if args.list_parks is not None:
        with ThemeParks(api_key=args.api_key, user_agent=USER_AGENT) as tp:
            return _print_list(_catalogue(tp), args.list_parks or None)

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
            "no API key: reading the 7 days anonymous access allows. Only final days\n"
            "  are written, and the newest 2 to 3 are still being recorded, so that\n"
            "  is usually 4 or 5 days per park.\n"
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

        status = _run_all(tp, targets, args)
        # SAID AGAIN AT THE END, and this is the point of it. The warning above
        # is printed before a run that takes minutes, so it scrolls away, and the
        # last thing on screen is "done: 433 rows" -- which for a customer who
        # thought they were downloading five years is indistinguishable from
        # success. They paid for 400 days and got seven, exit 0, no complaint.
        if not args.api_key:
            print(
                "\nthat was ANONYMOUS ACCESS: the final days among the last 7 days only,\n"
                "  usually 4 or 5 per park.\n"
                "  a free key reads 30 days, Pro 400, Business the whole archive\n"
                "  set THEMEPARKS_API_KEY and run the same command again\n"
                "  keys: https://www.themeparks.wiki/profile",
                file=sys.stderr,
            )
        return status
    return 0


def _run_all(tp: ThemeParks, targets: list[tuple[str, str]], args: Any) -> int:
    """Back fill every target, and report what did not finish.

    ONE PARK'S FAILURE IS NOT THE DESTINATION'S. A 500 on Animal Kingdom used to
    abandon the run, so the parks after it were never attempted: the customer got
    a partial download, a traceback, and no statement of which parks were
    missing. Every park is tried, what failed is named at the end, and the exit
    code still says something went wrong.
    """
    failed: list[str] = []
    window = _Range(getattr(args, "since", None), getattr(args, "until", None))
    for park_id, pname in targets:
        try:
            status = backfill_park(
                tp, _Park(park_id, pname), args.out, args.format, args.overwrite, window=window
            )
        except (ThemeParksError, OSError) as exc:
            print(f"{park_id}: {exc}", file=sys.stderr)
            failed.append(pname)
            continue
        if status == EX_TEMPFAIL:
            # A spent budget stops everything: the next park would spend the
            # retry-after for nothing, and every state file says where it got to.
            return status
        if status != 0:
            failed.append(pname)
    if failed:
        print(
            f"\n{len(failed)} of {len(targets)} did not finish: {', '.join(failed)}\n"
            f"  the rest are written. Run the same command again to retry just these.",
            file=sys.stderr,
        )
        return 1
    return 0


def cli() -> int:
    """The installed entry point: `main`, with no traceback for a bad request.

    An unreachable API, a timed-out connection or a mistyped id used to print a
    nine-frame traceback. A traceback is a bug report about this command; none of
    these are bugs in it, and a customer who has just paid reads one as the tool
    being broken.
    """
    previous = _stop_on_sigterm()
    try:
        return main()
    except KeyboardInterrupt as exc:
        # Nothing to save here. The state file was written after the last whole
        # page, and the next run cuts off anything written since, so stopping at
        # any point costs at most the page in flight.
        print(
            "\nstopped. Everything through the last whole page is saved; run the "
            "same command again to continue.",
            file=sys.stderr,
        )
        return 143 if isinstance(exc, _Terminated) else 130
    except (NetworkError, ApiTimeoutError) as exc:
        # A connection reset or a timeout IS resumable, so this is EX_TEMPFAIL and a
        # scheduler retries rather than alerting. The JavaScript SDK said 75 here
        # and Python said 1: opposite semantics for one event, on the number a cron
        # acts on.
        print(
            f"{exc}\n  the run is resumable: the same command continues it",
            file=sys.stderr,
        )
        return EX_TEMPFAIL
    except ThemeParksError as exc:
        # Anything the API actively rejected: a bad range, a missing id, a revoked
        # key. Retrying changes nothing, so this is a plain failure. Every SDK
        # failure descends from ThemeParksError, so a new kind cannot become a
        # traceback.
        print(f"{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # A full disk or an unwritable --out directory. Five years of one park is
        # a few hundred MB, so this is not hypothetical.
        print(f"cannot write the output: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


class _Terminated(KeyboardInterrupt):
    """SIGTERM, raised where the process is, so it unwinds like Ctrl-C.

    The default SIGTERM action ends the process without running any `finally`
    or `with` exit: the output file is not closed and nothing says what
    happened. A scheduler stopping a run (systemd, a container shutdown) sends
    exactly this, so it gets the same orderly stop and message as Ctrl-C, and
    exit 143, the conventional code for it.
    """


def _stop_on_sigterm() -> Any:
    """Turn SIGTERM into `_Terminated` for this process. Returns the old handler."""

    def handler(_signum: int, _frame: Any) -> None:
        raise _Terminated

    try:
        return signal.signal(signal.SIGTERM, handler)
    except ValueError:  # pragma: no cover - not the main thread
        return None


if __name__ == "__main__":
    raise SystemExit(cli())
