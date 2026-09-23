# Changelog

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
