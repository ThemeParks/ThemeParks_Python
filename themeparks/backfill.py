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
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
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
from themeparks._ergonomic.history import EntityRef, HistoryPage
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
#: is refused rather than guessed at.
STATE_VERSION = 1

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
    path.write_text(json.dumps(fields, sort_keys=True) + "\n", encoding="utf-8")


def _state_mismatch(state: dict[str, Any], fmt: str) -> str | None:
    """Why this state file cannot be resumed by this build, or None.

    camelCase keys, deliberately: the JavaScript SDK writes the same file and the
    two used to differ only in `last_day` vs `lastDay` -- the two keys that matter
    on the interrupted path. Everything else was spelled identically, so the safe
    paths interoperated and nothing warned, while a Python run interrupted at 64
    rows and resumed by the JavaScript command produced 172 rows with 64
    duplicated keys and `complete: true`.
    """
    if state.get("stateVersion") != STATE_VERSION:
        written = state.get("stateVersion")
        return f"it was written by a different version of this command (state v{written})"
    if state.get("sdk") != SDK_NAME:
        other = state.get("sdk")
        return f"it was written by the {other} SDK, and resuming across SDKs is not supported"
    if state.get("format") != fmt:
        return f"it is a {state.get('format')} run"
    if state.get("columns") != _columns_fingerprint(fmt):
        return "the column layout changed since it was written"
    return None


class _StateFile(NamedTuple):
    """Where the state lives and the range it describes, fixed for one park."""

    path: Path
    fmt: str
    start: Day
    end: Day


def _record(
    sf: _StateFile, last_day: date | None, resume_from: str | None, *, complete: bool
) -> None:
    """Write the state file. `complete` is the fact the old checkpoint could not express.

    `sf.start` is the ORIGINAL start of the range, not the day a resumed run
    happened to begin at. The two call sites used to disagree about that, so a
    run interrupted twice recorded the second resume point as though it were the
    beginning and lost the real range.
    """
    _write_state(
        sf.path,
        sdk=SDK_NAME,
        sdkVersion=PACKAGE_VERSION,
        stateVersion=STATE_VERSION,
        format=sf.fmt,
        columns=_columns_fingerprint(sf.fmt),
        # None, never the string "None". `str(None)` put the literal "None" in
        # the file where the JavaScript SDK writes null, and "None" is truthy.
        start=_day_str(sf.start),
        end=_day_str(sf.end),
        lastDay=last_day.isoformat() if last_day else None,
        resumeFrom=resume_from,
        complete=complete,
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
    prior_start: str | None
    #: True when rows from an EARLIER run are already in the file. Every deletion
    #: in this module has to consult it: `written == 0` means "this process wrote
    #: nothing", which on a resumed run is not the same as "the file is empty".
    resumed: bool = False


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
    mismatch = _state_mismatch(state, fmt) if state else None
    resumable = bool(state) and mismatch is None

    # Finished already. Say so and stop, rather than appending a second copy.
    if state.get("complete") and resumable and file_exists:
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

    # A state file this build cannot resume. Refusing is the only safe answer: the
    # file beside it was written to a different contract, and appending to it
    # produces a file no reader can parse -- or worse, one that parses wrongly.
    if state and mismatch is not None and not state.get("complete"):
        print(
            f"  there is an unfinished {out_path.name} beside this state file, but "
            f"{mismatch}.\n"
            f"    --overwrite   start this park again from the beginning\n"
            f"    or move both files aside and run again",
            file=sys.stderr,
        )
        return 1

    resuming = resumable and not state.get("complete")
    # THE PAGE BOUNDARY, not the newest row. `last_day` is the highest date
    # written; the page it came from covered further, because an entity that
    # stopped reporting has no rows for the tail days. Resuming at `last_day`
    # re-fetches a day already in the file and appends every row of it again --
    # on the exit-75 path, which is the ordinary path for a long back fill, and
    # it breaks the (entityId, date) key the file is documented to have.
    #
    # `last_day` stays as the fallback for the two cases with no boundary
    # recorded: a state file written by 3.3.0, and a run that died part-way
    # through its FIRST page. One duplicated day beats starting from the top and
    # appending a second copy of the whole archive.
    resume_at = (state.get("resumeFrom") or state.get("lastDay")) if resuming else None
    return _Plan(
        start=resume_at or archive_from,
        has_rows=file_exists and resuming,
        prior_start=state.get("start") if resuming else None,
        resumed=resuming,
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
        self.resume_from: str | None = None
        self.skipped = False


def _stream(job: _Job, start: Day, progress: _Progress) -> None:
    """Write the range to the file, recovering once from a window 403.

    Separated from `backfill_park` because that function was deciding, printing,
    streaming and recording in one place, and ruff counted the statements before
    a reader had to. This is the streaming.
    """

    def note_page(page: HistoryPage) -> None:
        """Checkpoint, called once every row of a page is written.

        The day the NEXT page starts on, taken from the server's own `next` URL,
        so a resumed run asks for nothing twice. None on the last page, where
        there is nothing left to carry on from.
        """
        progress.resume_from = _next_page_start(page.next_url)

    def write_rows(writer: Writer, first_day: Day) -> None:
        """Stream one range into the file. Raises whatever the SDK raises."""
        for ref, row in job.history.days_with_entities(first_day, job.end, on_page=note_page):
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
    out_path: Path, state_path: Path, sf: _StateFile, progress: _Progress, *, resumed: bool
) -> int:
    """The park had nothing in this key's window. Tidy up, or refuse to.

    On a first run both files go: an empty file reads as "this park has no
    history". On a RESUMED run an earlier run's rows are real and are not ours to
    remove, so the state is kept and the exit code says the run did not finish.
    """
    if resumed:
        _record(sf, progress.last_day, progress.resume_from, complete=False)
        return 1
    out_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return 0


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
    state_path = state_path_for(out_dir, park_id, fmt)
    end = span.retrievable_through

    decided = _decide(out_path, state_path, fmt, overwrite, span.archive_from)
    if isinstance(decided, int):
        return decided
    start, has_rows, prior_start, resumed_run = decided
    resuming = prior_start is not None
    sf = _StateFile(state_path, fmt, prior_start or start, end)

    print(
        f"{park_id}: {start} .. {end}{' (resumed)' if resuming else ''} -> {out_path}",
        file=sys.stderr,
    )

    if _is_empty_window(start, end):
        return _window_closed(out_path, end, start, resumed=resumed_run)

    ident = _RowIdentity(park)
    job = _Job(history, out_path, fmt, end, has_rows, ident)
    progress = _Progress()
    try:
        _stream(job, start, progress)
    except BudgetExhaustedError as exc:
        # The budget is hourly, so a spent one can be most of an hour from
        # resetting. Record how far we got and exit 75 rather than sleeping.
        _record(sf, progress.last_day, progress.resume_from, complete=False)
        if progress.written == 0 and not resumed_run:
            # A budget spent before the first page left a 0-byte file that reads
            # as "this park has no history".
            out_path.unlink(missing_ok=True)
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
        # Every other failure still records where it got to, or the next run
        # starts over and appends a second partial copy. And AN EMPTY FILE IS A
        # LIE: opening the file created it before the first request, so a park
        # that failed with nothing written left a 0-byte file that reads as
        # "this park has no history" -- on a six-park destination the customer
        # counts six files and never sees which one is empty.
        if progress.last_day is not None:
            _record(sf, progress.last_day, progress.resume_from, complete=False)
        # `written` counts rows THIS process wrote, so on a resumed run it is 0
        # while the file holds everything the previous runs fetched. Deleting it
        # there destroyed the archive and left the state file pointing into the
        # middle of it, so the next run appended only the tail and recorded
        # `complete: true`.
        if progress.written == 0 and not resumed_run:
            out_path.unlink(missing_ok=True)
        raise

    if progress.skipped and progress.written == 0:
        return _nothing_written(out_path, state_path, sf, progress, resumed=resumed_run)

    # Completion is RECORDED, never inferred from a missing file. That is the
    # distinction the old checkpoint could not make.
    _record(sf, progress.last_day, None, complete=True)
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

        return _run_all(tp, targets, args)
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
    for park_id, pname in targets:
        try:
            status = backfill_park(tp, _Park(park_id, pname), args.out, args.format, args.overwrite)
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
    try:
        return main()
    except KeyboardInterrupt:
        print("\nstopped. Run the same command again to continue.", file=sys.stderr)
        return 130
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


if __name__ == "__main__":
    raise SystemExit(cli())
