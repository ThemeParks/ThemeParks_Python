# Changelog

## [Unreleased]

### Added

- **`themeparks-backfill --since YYYY-MM-DD` and `--until YYYY-MM-DD`.** A key
  that reaches the whole archive used to download all of it, every time: a
  buyer who wanted the last twelve months got five years. Both days are
  inclusive and must be real calendar days written `YYYY-MM-DD`; `--since`
  after `--until` is refused before anything is requested. `--since` applies when a file is started. A later run accepts the
  same `--since` or a later one (a cron line computing "30 days ago" works), and
  refuses one earlier than the file's first day, or one that would leave a gap,
  instead of quietly handing back a file that is not what was asked for.

- **`history.changes()` exposes the `opening` state.** The raw history
  response carries, per entity, the state in force at the start of the range,
  and `changes()` threw it away. Without it the minutes between midnight and an
  entity's first change had no known status, so a day rebuilt from raw history
  disagreed with the daily summary whenever a ride was still running from the
  night before. The result of `changes()` now has `opening`, a dict of
  `HistoryOpening` keyed by entity id, covering every entity in the response,
  including one that did not change all day. Iterating it yields exactly what it
  always did, and reading `opening` costs no extra request. The async client's
  result has the same attribute once the response has arrived: iterate first,
  or `await changes.load()`.

- **`HistorySpan.final_through`**: the newest day whose daily row will not
  change again, the earlier of `recorded_to` and `retrievable_through`. A
  property, so `a, b, c = span` still works.

### Fixed

- **A finished park now updates on the next run.** A rerun used to print
  `already complete` and exit 0 without fetching a single new day, so a nightly
  cron looked healthy and never updated; the only way to get yesterday was
  `--overwrite`, which downloaded the whole archive again. A finished file is now
  carried forward from the day after its last one, appending only the new days.
  An interrupted run still resumes exactly where it stopped, including a rerun
  interrupted before its first page, which would otherwise have started again
  from the top of the archive.

- **The newest rows of a backfill were partial days, and stayed that way.** A run
  ended on `retrievableThrough`, which is usually today: today's row is the day
  so far, and the archive records days 2 to 3 behind live data, so the last few
  days of every file were still changing when they were written. Magic Kingdom's
  last day summed to about half the operating minutes of a full one. A run now
  ends at `final_through`, says so when it holds days back, and the next run adds
  them once they are final. Every row in the file is one that will not change.

  **Files written by 4.0.x are corrected once.** Their state file does not say
  which of their newest days were final, so the first run of this version
  removes the rows from the last seven days before that run's end and fetches
  those days again. Every other row is left byte for byte as it was. A 4.0 file
  whose newest row is older than that is not rewritten at all, and one that lies
  wholly inside those seven days, as every anonymous 7-day file does, is simply
  downloaded again. The state file format moves to version 2 for this; version 1
  files from this SDK are upgraded, not refused.

- **A continued file could skip ahead to the key's first day.** When the day a
  file continues from is older than the key may read, because a cron missed more
  days than the window or a plan lapsed, the run carried on from the key's first
  day and left a gap the state file did not record. It is refused now with exit
  1, the file and its state untouched, and a message naming `--overwrite`. A
  fixed `--since` older than the window is not affected: the file starts at the
  key's first day, and the same command line keeps working every night.

- **A finished file written to a different column layout was appended to.** Only
  an unfinished one was refused. A finished one fell through to a fresh start,
  which opened the existing file in append mode and wrote the whole archive into
  it a second time under a second header, exit 0. It is refused now, the same
  way.

- **A state file whose data file had been deleted was continued**, producing a
  file that started part-way through its range and was then recorded as
  complete. The park is downloaded again from the start instead.

- **The README said `waitTime` is an `int`.** The API's schema declares it a
  JSON `number`, the models type it `float`, and a raw row dumped to JSON says
  `45.0`. The README now says `float | None`, explains the `45.0`, and a test
  holds its table to the models' types.

## [4.0.1] - 2026-09-28

### Fixed

- **A run with no API key now says so when it finishes**, not only when it
  starts. Without a key the command SUCCEEDS: it reads the 7 days anonymous
  access allows, writes 433 rows of Magic Kingdom instead of about 94,000, and
  exits 0. The notice was printed before a run that takes minutes, so it scrolled
  away, and the last thing on screen was `done: 433 rows` -- which for someone
  who has just paid for 400 days is indistinguishable from success. There is a
  file, there is no error, and the number means nothing unless you already know
  what it should have been.

  The README and the command's own `--help` now `export THEMEPARKS_API_KEY`
  before the example that needs it, and still say `--list` does not: finding a
  park before you have paid is the point of that flag.

### Added

- **A committed mutant list** (`tests/mutation/mutants.json`) and a nightly,
  non-gating job that runs it. An author-written mutant list contains the
  mutations that author's tests already catch -- one scored 14/14 on this package
  while an independent sweep found 27 survivors, including the defect 4.0.0
  exists to fix. The list is committed so a reviewer can see what is checked and,
  more usefully, what is not. Its first run found two gaps, both closed here: a
  spent hourly budget on a resumed run deleted the accumulated archive, and
  `str(EntityType.SHOW)` reached a CSV cell as `EntityType.SHOW` rather than
  `SHOW`.

## [4.0.0] - 2026-09-28

**3.3.0 was yanked: incomplete CSV export and a resume defect.**

A major version because **the CSV header changed**: fifteen columns were added and
the order is now the schema's, so a reader that takes columns by position gets the
wrong ones rather than an error. Read by name. The library API is backward
compatible.

Everything here came out of porting `themeparks-backfill` to the JavaScript SDK
and then diffing the two outputs over the same park, and out of six reviews of the
result. Two independent implementations reading one API disagree in exactly the
places one of them is wrong. Magic Kingdom's full archive now comes back
**byte for byte identical** from both SDKs: 94,223 rows, 41 columns, the only
differences being today's row, which grows as the day elapses.

### Fixed

- **`themeparks-backfill "magic kingdom"` wrote the wrong park name into every
  row.** A name that matched one park by substring returned the formatted display
  label, so `parkName` read `Magic Kingdom Park  (Walt Disney World® Resort)` for
  all ~94,000 rows, and the resolution echo printed the destination twice. Four
  live names reached it.

- **The CSV was missing ten of the thirty-six fields the API sends, on every row.**
  `unknownMinutes`, the whole `inParkHours` block (the day's numbers limited to the
  park's published hours -- usually the ones you want, since a ride "down" at 2am
  is not down), `extremeWaits` (how many readings of 480+ minutes are folded into
  the statistics, which is how you spot a feed error), and three of `singleRider`'s
  five percentiles while `standby` carried all five. On a five-year Magic Kingdom
  export, 72,200 of 94,223 rows were missing their in-park statistics. **The column
  list is now derived from the model**, so it cannot drift again.

- **Vendored models were stale, and pydantic drops what it does not declare**, so
  those three fields were deleted at parse time for every caller of `days()`, not
  just for the CSV. Models regenerated, and every model now keeps fields the schema
  does not declare (`themeparks._models_base.ApiModel`, `extra="allow"`), so a
  field the API adds tomorrow survives parsing and reaches `model_dump()` and the
  NDJSON output before this SDK knows it exists. It does not reach the CSV, whose
  columns come from the schema.

- **A resumed download duplicated a day.** The checkpoint was the newest row
  written; the page it came from covered further, because an entity that stopped
  reporting has no rows for the tail days. A rerun re-fetched a day already in the
  file and appended every row of it again, breaking the `(entityId, date)` key --
  on the exit-75 path, which is the ordinary path for a long back fill. The
  checkpoint is now the day the server's own `next` URL starts on.

- **A failure on a resumed run deleted everything already downloaded.** `written
  == 0` means "this process wrote nothing", not "the file is empty". The state file
  survived pointing mid-archive, so the next run appended only the tail and
  recorded `complete: true`. Same for a window that closes under a resumed run --
  a key rotated out of a scheduler's environment, a lapsed subscription -- which
  additionally exited 0, so the scheduler logged success, and became a permanent
  trap.

- **Resuming across versions, formats or SDKs corrupted the file.** One state file
  served both formats, so `ndjson` then `csv` then `ndjson` doubled every row in
  the first file; and the state carried nothing about the header, so 3.3.0's
  19-column file resumed under this build appended 41-field rows beneath it. The
  state file is now `<parkId>.<format>.backfill-state.json` and records the SDK,
  its version, a state version and a fingerprint of the exact header. Anything that
  does not match is refused with a message saying why, never resumed.

- **A network failure or timeout now exits 75, not 1**, so a scheduler retries
  rather than alerting; anything the API actively rejected still exits 1. The
  JavaScript SDK had these the other way round.

- **A carriage return in an entity name was written unquoted on Python 3.9 and
  3.10**, so one row parsed as two with every later column shifted. The `csv`
  module's QUOTE_MINIMAL only quotes characters that appear in the line terminator,
  and this command sets LF; 3.11 changed the module to always quote CR and LF, so
  the defect was invisible on a modern interpreter and live on two supported ones.
  The CSV writer now does its own minimal quoting, which also makes the output
  byte-identical across Python versions rather than only within one.

- **UTC timestamps are written `Z`, not `+00:00`**, and CSV line endings are LF.
  Between them these accounted for 39,201 differing lines against the JavaScript
  SDK's output for no difference in meaning.

- **One park's failure no longer abandons the rest of a destination.** Every park
  is tried, what failed is named at the end, and the exit code still says something
  went wrong. A spent budget still stops everything, deliberately.

- **A failed park no longer leaves a 0-byte file** that reads as "this park has no
  history", including when the budget runs out before the first page.

- **A network failure, a full disk or Ctrl-C is a sentence, not a traceback.**

- **The user agent named neither version.** It was the literal
  `themeparks-backfill/1`, and it replaced the SDK's own, so a support question had
  no version to work from at either end.

- **`--list <text>` reported the wrong total**, printing "all 1 parks" for a
  destination with six -- on the one line whose whole job is that number.

- **An ambiguous name listed the wrong candidates**, widening to substrings and
  offering a third park that was not what was typed. It now lists the ids of the
  parks that actually match, sorted by name.

- **A collection of nested models would have produced phantom columns** and then an
  `AttributeError` on the first row. Duplicate column names are now impossible at
  import rather than a wrong number under a right-looking header.

- **The NDJSON identity columns could be overwritten by the row** once models kept
  undeclared fields.

### Added

- **The CSV carries a UTF-8 BOM**, so Excel on Windows stops rendering
  `Walt Disney World® Resort` as mojibake.
- **A cell a spreadsheet would execute is prefixed with an apostrophe** (`=`, `+`,
  `-`, `@`, tab, CR). Numeric cells are left alone, so a negative number stays a
  number.
- **`on_page` on `days()` and `days_with_entities()`**, called once every row of a
  page has been yielded, with a `HistoryPage` (`start`, `end`, `next_url`). The page
  boundary is the server's own answer to "where do I carry on", and the rows cannot
  tell you.
- **`--version`.**
- `EntityRef` and `HistoryPage` are exported from the package.
- `tests/fixtures/csv_contract.json`, an identical copy of which lives in the
  JavaScript SDK. Both suites assert their column list against it, because this is
  one command with two implementations and a customer using both should get one
  file format.

### Changed

- The `themeparks-backfill` entry point is `themeparks.backfill:cli`, which adds
  the top-level error handling. `main()` is unchanged for anyone calling it.
- Model equality and `model_json_schema()` reflect `extra="allow"`: two responses
  differing only in an undeclared field now compare unequal, and dumps may contain
  fields the schema does not list.

## [3.3.0] - 2026-09-28

### Added

- **`themeparks-backfill`: the archive download as a command.** It was an example
  to copy off GitHub. The first paying customer followed that link and had to
  work out that the library needed installing, then what the arguments were,
  then read a traceback. Now:

  ```bash
  pip install themeparks
  themeparks-backfill "Disneyland Park"
  ```

  - Takes a park or a **destination**, by name or id. A destination back fills
    every park in it, one file each. `"Walt Disney World Resort"` is the handle
    people actually have; four park uuids is not.
  - `--list [text]` prints destinations with their parks underneath, and **needs
    no key**, so you can find your park before deciding whether to pay.
  - Refuses to guess between two matches. Two parks are named exactly
    "Disneyland Park" (Anaheim and Paris), so the candidate list names the
    destination as well.
  - **Runs without a key**, reading the 7 days anonymous access allows, and says
    what a key would add. It used to refuse to start with a message that
    mentioned anonymous access in the same breath.
  - NDJSON by default, `--format csv` for one wide row per entity per day.
  - Checkpoints against the hourly history budget and exits 75 (`EX_TEMPFAIL`),
    so a cron or timer retries rather than alerting. Re-running continues.

  `python -m themeparks.backfill` is the same thing. `examples/backfill.py`
  remains as a shim so existing links keep working.

### Fixed

- **The history window recovery now actually works.** 3.2.0's `examples/backfill.py`
  read `earliestAllowedDate` from the top level of the 403 body; the API nests it
  under `error`. So the recovery shipped doing nothing and a Pro customer still
  got a traceback on their first request. The tests passed because the fixture was
  built from the formatted text in a traceback rather than a real response, so the
  code and the test were wrong together. The fixture is now captured from
  production and a test fails if anyone flattens it.

  The underlying gap is in the API, not the client: `/history/coverage` reports
  where the archive starts and where your window ends, and nothing about where
  your window begins. Until it does, the 403 is the only place that date exists.

## [3.2.0] - 2026-09-26

### Added

- **The client reads the rate-limit headers, and acts on them.** Both meters,
  the per-minute REST one and the separate hourly history budget, are exposed
  on `client.rate_limit`:

  ```python
  tp.rate_limit.rest.remaining          # 299
  tp.rate_limit.history.remaining       # on a history call
  tp.rate_limit.rest.seconds_until_reset()
  ```

  Every field is optional, and `None` means the server did not say rather than
  "nothing left". Use `.exhausted`, which is true only when the server said
  zero. The per-minute figures ride most responses; the hourly history ones are
  withheld from anything a shared cache may store, because they are per-caller;
  an unmetered plan advertises nothing. A response served from a cache is
  ignored entirely, because its figures belong to whoever populated the entry. `reset` is a
  relative countdown frozen when it was read, so `seconds_until_reset()` ages
  it rather than returning a stale number.

  When a response says the window is spent, the next request now waits for the
  advertised reset instead of sending one that is certain to be refused, and to
  spend a unit of budget being refused. `RetryConfig(respect_remaining=False)`
  turns it off.

  The hourly history budget is new on the wire; before it there was nothing to
  read.

### Changed

- **Calls may now block before sending.** When the server has said your window
  is spent, or has issued a 429 that is still in force, the client waits rather
  than sending a request that is certain to be refused. A call that used to
  return in 200ms can now take up to `retry.max_retry_after` (120s) first. That
  is a TOTAL across the call, not per wait: the shared 429 gate and the
  spent-window wait stack, and before the budget existed a 429 carrying both a
  `Retry-After` and a spent window blocked for 180 seconds under a 120 second
  cap. Turn the two halves off with `RetryConfig(respect_remaining=False)` and
  `RetryConfig(respect_429=False)`.

### Fixed

- **`respect_429=False` did not opt out.** It raised the error the caller asked
  for and then held their NEXT call for the full `Retry-After` anyway, because
  the shared gate was closed regardless of the setting.

- **A 429 was waited out once per in-flight request.** The wait belongs to the
  caller, not to whichever request met it, so ten concurrent requests each
  slept their own `Retry-After` and then retried at the same instant,
  re-tripping the limit together. It is now taken once, on a gate shared by the
  whole client, with a little jitter so the waiters do not wake in unison. A
  shorter wait arriving while a longer one is in force no longer brings the
  gate forward.

## [3.1.0] - 2026-09-23

### Added

- **History.** `tp.entity(id).history` reads the archive, and pages for you:

  ```python
  with ThemeParks(api_key=KEY) as tp:
      history = tp.entity(DISNEYLAND).history
      span = history.span()
      for entity_id, row in history.days(span.archive_from, span.retrievable_through):
          ...
  ```

  - `span()` returns `archive_from`, `recorded_to` and `retrievable_through`
    in one shape. The underlying coverage documents do not: a park nests them
    under `summary`, an entity carries them at the top level under different
    names, so without this every caller writes that branch first.
    `retrievable_through` is the end date to bound a backfill by, because it
    is what the key may read rather than what the archive holds.
  - `days(start, end)` yields `(entity id, row)` for one summary row per
    park-local day; `changes(date)` yields every recorded observation.
    Both follow the server's paging links to the end and yield as they go, so
    a resort's five years never has to be in memory at once.
  - Given a park id, both use the park-level call, which answers every entity
    in the park in one request. The same data fetched ride by ride is around a
    hundred times more calls against the same budget.
  - `BudgetExhaustedError` (a `RateLimitError`) is raised when the history
    budget is spent and the server asks for a longer wait than `max_wait`
    (120s by default). It carries `retry_after`, so a backfill can checkpoint
    and resume rather than hold a process open for most of an hour.

- **`examples/backfill.py`** — a complete backfill with resume and NDJSON or
  CSV output. It pulls Disneyland Resort's whole daily archive, 98,452 rows,
  in one run.

### Fixed

- **A 429 could park the client for hours.** The transport honoured any
  `Retry-After` up to `max_retries` times. That is right for a REST 429, which
  asks for seconds, and wrong for a history 429: that budget is hourly, so a
  spent one can ask for most of an hour, and three of those is roughly two and
  a half hours of a silent process. `RetryConfig` gains `max_retry_after`
  (120s by default): past it the client does not sleep at all and raises
  `RateLimitError` with `retry_after` set. Without this `BudgetExhaustedError`
  was unreachable in practice, because the transport rode out the wait before
  the history layer ever saw the 429.

- **The user agent announced the wrong version.** `PACKAGE_VERSION` was a
  literal reading `2.0.0` in a package at `3.1.0`, so every request this SDK
  has made since 3.0.0 named a version two majors old, and nothing anywhere
  failed. It is now read from the installed package metadata, which cannot
  drift, and a gate test pins it to `pyproject.toml` and to the `User-Agent`
  the transport builds.

- **The client had no way to send an API key.** There was no `api_key`
  parameter anywhere, and the transport sent only `user-agent` and `accept`,
  so every request this SDK made was anonymous: the lowest rate limit and the
  most recent seven days of history, whatever the caller had paid for. A
  paying customer had to drop to raw `httpx` to use their own plan.
  `ThemeParks(api_key=...)` and `AsyncThemeParks(api_key=...)` now send
  `x-api-key`. An empty string is treated as no key, because an unset
  environment variable arrives as `""` far more often than as `None`, and
  sending an empty key is a 401 rather than an anonymous request.

- **The model generator was silently under-patching every documented class.**
  `scripts/regenerate.py` restores nullability that `datamodel-code-generator`
  drops, by matching the field line inside its class. The pattern could not
  cross the blank line after a class docstring, so it matched only classes
  without one and left every documented class unpatched, printing a warning
  nobody read. That included `next` on all four history envelopes, which is
  null on the last page of every paged response, so the SDK would have failed
  to parse the page that ends a backfill. The pattern now spans blank lines,
  an unmatched patch is a hard failure rather than a warning, and the 22
  fields the spec marks both required and nullable are all listed.

## [3.0.0] - 2026-09-08

### Fixed

- **Schedule entries now carry `purchases`.** The upstream spec described a
  park's schedule two different ways: precisely when nested under a
  destination, loosely when fetched directly. This client uses the direct
  path, so `purchases` was absent from the model entirely. Magic Kingdom
  served 26 of 79 upcoming entries with purchases on the day this shipped.

  ```python
  sched = client.entity(park_id).schedule.upcoming()
  for day in sched.schedule or []:
      for p in day.purchases or []:
          print(day.date, p.name, p.price.amount, p.price.currency)
          # 2026-09-08 Lightning Lane for Seven Dwarfs Mine Train 1100.0 USD
  ```

  Purchases are not limited to `TICKETED_EVENT` days — Lightning Lane entries
  attach to ordinary `OPERATING` days, so do not filter on `type` to find
  them.

- **A null price on a schedule purchase no longer rejects the response.**
  2.0.1 made `PriceData.amount` nullable, but the schedule path used a
  second, inline price model that kept `amount` non-nullable. Both now use
  `PriceData`. Tokyo Disneyland serves six Premier Access rows with a null
  amount, and this client raised `ValidationError` on every one of them.

- Schedule entries gained the `description` field the API has always sent.

### Changed

- **BREAKING — `ScheduleEntry.type` is an enum, not a `str`.** It was
  `type: str`; it is now a `Type` enum. This breaks *silently*: the comparison
  does not raise, it just stops being true.

  ```python
  # before: True.  now: False, with no error.
  if day.type == "OPERATING":
      ...

  # use one of these instead
  if day.type.value == "OPERATING":
      ...
  from themeparks._generated.models import Type
  if day.type is Type.OPERATING:
      ...
  ```

  Audit any comparison of `.type` against a string literal before upgrading.
  This is the one change here that will not announce itself.

- **BREAKING — the `PricedScheduleEntry` and `Price` models are gone.**
  `PricedScheduleEntry` is now `ScheduleEntry`, and the inline `Price` model
  is replaced by `PriceData`. Neither was exported from the package root, so
  this only affects code importing from `themeparks._generated.models`
  directly — a private module.

- The duplicate `EntityType1` and `EntityType2` enums collapse into
  `EntityType`. Same members, same values.

## [2.0.1] - 2026-09-01
### Fixed
- `PriceData.amount` is now nullable. The API returns `null` when a paid queue
  exists but the provider does not publish a price, and `0` only when the queue
  is genuinely free. Both previously arrived as `0`, so an unknown price was
  indistinguishable from a free one.

  Before this, a single null amount on one attraction made pydantic reject the
  **entire** live response for that park, not just the one field.

  Code testing `if price.amount:` or `if not price.amount:` now conflates
  "free" with "price unknown" — the exact confusion this change exists to
  remove. Switch to an explicit check:

  ```python
  if price.amount is None:
      label = "price not published"
  else:
      label = f"{price.currency} {price.amount / 100:.2f}"
  ```

  `price.formatted` is optional and may be absent when the amount is unknown,
  so do not rely on it alone to detect the case.

## [2.0.0] - 2026-04-15
First stable v2 release. Identical surface to `2.0.0a1` after a brief alpha
soak; no code changes since `2.0.0a1`. Bumped `Development Status` classifier
to `Production/Stable`.

## [2.0.0a1] - 2026-04-15
### Added
- MkDocs Material documentation site with full API reference and a cookbook
  (recipes for sorted wait times, 7-day schedules, geo-locations grouped by
  entity type, every queue variant, and HTTP debugging).
- Top-level exports for `current_wait_time`, `iter_queues`, `parse_api_datetime`.
- Class-level docstrings on every public surface for readable API reference rendering.
- README sections explaining every queue variant (STANDBY, PAID_RETURN_TIME,
  BOARDING_GROUP, etc.) and how to enable the httpx logger for HTTP debugging.

### Fixed
- `walk()` now makes a single API call instead of one per descendant — the
  `/children` endpoint already returns the entire subtree recursively. Walking
  Walt Disney World dropped from ~250 requests to 1.
- `get_entity_schedule_month` now zero-pads the month (`/schedule/2026/05`,
  not `/schedule/2026/5`) per the API requirement.
- `RetryConfig` field renamed `max_attempts` → `max_retries` to match its
  actual semantics (N retries beyond the first attempt = N+1 total calls).
- `APIError` now includes a server-body excerpt in the exception message;
  `RateLimitError.__repr__` includes `retry_after`.
- `destinations.find()` now performs the loose, case-insensitive substring
  match it had always promised in its docstring (was exact equality).
- Generated queue variant classes renamed (`STANDBY` → `StandbyQueue` etc.)
  so users access `queue.STANDBY` instead of the awkward `queue.STANDBY_1`.
- `eval-type-backport` is now a conditional dep on Python 3.9 so pydantic
  can evaluate PEP 604 union syntax in the generated models.

## [2.0.0a0] - 2026-04-14
### Added
- Full rewrite on pydantic v2 + httpx.
- Sync `ThemeParks` and `AsyncThemeParks` clients with shared core.
- Ergonomic `client.entity(id)` navigation, `walk()`, `schedule.range()`.
- Typed pydantic models, correctly handling nullable queue fields (fixes #1, #2).
- Default-on caching with per-endpoint TTLs and pluggable adapter.
- 429 `Retry-After` handling.

### Removed
- Legacy `openapi_client` top-level import and generated surface. See MIGRATION.md.
- `urllib3`-based transport.
