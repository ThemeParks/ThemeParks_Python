# Changelog

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
