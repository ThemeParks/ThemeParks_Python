# Changelog

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
