# themeparks

A typed, modern Python SDK for the [ThemeParks.wiki](https://api.themeparks.wiki)
API. Built on `httpx` and `pydantic` v2, with first-class sync **and** async
clients, default-on caching, and ergonomic helpers for the common workflows
(list destinations, walk a park's children, fetch live wait times, pull a
date-ranged schedule).

## Install

```bash
pip install themeparks
```

Python 3.9+ is required.

## Quickstart (sync)

```python
from themeparks import ThemeParks

# Magic Kingdom (Walt Disney World)
MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

with ThemeParks() as tp:
    park = tp.entity(MAGIC_KINGDOM).get()
    print(park.name, park.entityType)

    live = tp.entity(MAGIC_KINGDOM).live()
    for item in live.liveData or []:
        standby = item.queue.STANDBY if item.queue else None
        wait = standby.waitTime if standby else None
        print(f"{item.name:40s}  {wait} min" if wait is not None else f"{item.name:40s}  (closed)")
```

## Quickstart (async)

```python
import asyncio
from themeparks import AsyncThemeParks

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

async def main():
    async with AsyncThemeParks() as tp:
        park = await tp.entity(MAGIC_KINGDOM).get()
        print(park.name)

        live = await tp.entity(MAGIC_KINGDOM).live()
        print(f"{len(live.liveData or [])} live items")

asyncio.run(main())
```

The async client mirrors the sync surface. Only the call sites need `await`.

## Client options

Both `ThemeParks` and `AsyncThemeParks` take the same keyword-only options:

| Option       | Type                                     | Default                              | Purpose |
|--------------|------------------------------------------|--------------------------------------|---------|
| `base_url`   | `str`                                    | `https://api.themeparks.wiki/v1`     | API base URL (point at a mock / staging if you need to). |
| `user_agent` | `str \| None`                            | `themeparks-sdk-py/<version>`        | Sent as the `User-Agent` header. Set this to identify your app. |
| `timeout`    | `float` (seconds)                        | `10.0`                               | Per-request timeout. |
| `retry`      | `RetryConfig \| None`                    | `RetryConfig(max_retries=3, respect_429=True)` | Retry/backoff behavior. `max_retries` is N retries beyond the first attempt (so N+1 total calls). |
| `cache`      | `Cache \| CacheConfig \| bool \| None`   | `True` (in-memory LRU)               | See **Caching** below. `False` disables caching entirely. |

Example:

```python
from themeparks import ThemeParks, RetryConfig

tp = ThemeParks(
    user_agent="my-app/1.2.3 (+https://example.com)",
    timeout=15.0,
    retry=RetryConfig(max_retries=5, respect_429=True),
)
```

## Ergonomic helpers

```python
from datetime import date
from themeparks import ThemeParks

with ThemeParks() as tp:
    # Directory lookup
    wdw = tp.destinations.find("waltdisneyworldresort")
    print(wdw.id, wdw.name)

    # Walk a destination and yield every descendant (parks, lands, attractions, ...)
    for child in tp.entity(wdw.id).walk():
        print(child.entityType, child.name)

    # Schedule across a date range (stitches monthly responses and filters)
    mk = "75ea578a-adc8-4116-a54d-dccb60765ef9"
    entries = tp.entity(mk).schedule.range(date(2026, 5, 1), date(2026, 5, 31))
    print(f"{len(entries)} schedule entries")
```

## Low-level escape hatch

Every ergonomic helper is built on top of `tp.raw`, which is a thin, typed
1:1 wrapper over the OpenAPI operations. Use it directly when you want the
raw response shape:

```python
with ThemeParks() as tp:
    live = tp.raw.get_entity_live("75ea578a-adc8-4116-a54d-dccb60765ef9")
    dests = tp.raw.get_destinations()
    children = tp.raw.get_entity_children(wdw.id)
```

The raw methods return `pydantic` models, so you still get full type checking
and attribute access.

## Error handling

All SDK errors inherit from `ThemeParksError`. The ones you will want to
catch in application code:

```python
from themeparks import ThemeParks, APIError, RateLimitError, NetworkError, TimeoutError

with ThemeParks() as tp:
    try:
        live = tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
    except RateLimitError as exc:
        # 429; exc.retry_after is seconds if the server told us
        print(f"rate limited, retry after {exc.retry_after}s")
    except APIError as exc:
        # any non-2xx status
        print(f"api error {exc.status} at {exc.url}: {exc.body}")
    except (NetworkError, TimeoutError) as exc:
        # transport failure or slow server
        print(f"transport: {exc!r}")
```

`RateLimitError` is a subclass of `APIError`, so the order of the `except`
blocks matters if you want to handle 429 specially.

## Caching

The default client caches `GET` responses in-memory with sensible per-endpoint
TTLs:

| Endpoint                                | TTL        | Rationale |
|-----------------------------------------|------------|-----------|
| `GET /destinations`                     | 1 hour     | Directory rarely changes. |
| `GET /entity/{id}`                      | 1 hour     | Entity metadata is static. |
| `GET /entity/{id}/children`             | 1 hour     | Park topology is stable. |
| `GET /entity/{id}/schedule[/yyyy/mm]`   | 5 minutes  | Schedules update but not rapidly. |
| `GET /entity/{id}/live`                 | 0 (bypass) | Live data is always fetched. |

### Disable caching

```python
tp = ThemeParks(cache=False)
```

### Plug in your own adapter

`Cache` is a `Protocol`; any object implementing `get`, `set`, and `delete`
works. Here is a minimal `dict`-backed example (for real-world use you would
want TTL enforcement and bounded size):

```python
from typing import Any
from themeparks import ThemeParks, Cache

class DictCache:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._data.get(key)

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

tp = ThemeParks(cache=DictCache())
```

The per-endpoint TTL table is applied by the transport layer, so your adapter
receives the correct `ttl_seconds` for each call and can honor it however it
likes (Redis `EXPIRE`, filesystem mtime, etc.).

## What's new in v2

v2 is a full rewrite on `httpx` + `pydantic` v2. It replaces the generated
`openapi_client` surface with a hand-crafted client, fixes the nullable
queue-field crash from issues #1 and #2, and adds native async support.

See [MIGRATION.md](./MIGRATION.md) for a side-by-side v1 to v2 guide.

## Supported Python versions

3.9, 3.10, 3.11, 3.12, 3.13.

## Links

- API docs: https://api.themeparks.wiki
- Issues: https://github.com/ThemeParks/ThemeParks_Python/issues
- Changelog: [CHANGELOG.md](./CHANGELOG.md)

## License

MIT.
