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

## Print live wait times (sync)

```python
from themeparks import ThemeParks, current_wait_time

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

with ThemeParks() as tp:
    live = tp.entity(MAGIC_KINGDOM).live()
    for entry in sorted(live.liveData or [], key=lambda e: e.name):
        wait = current_wait_time(entry)
        if wait is None:
            print(f"{entry.name:50s}  --")
        else:
            print(f"{entry.name:50s}  {wait:>3d} min")
```

Sample output:

```
Astro Orbiter                                       15 min
Big Thunder Mountain Railroad                       45 min
Buzz Lightyear's Space Ranger Spin                  20 min
...
```

## Print live wait times (async)

```python
import asyncio
from themeparks import AsyncThemeParks, current_wait_time

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

async def main() -> None:
    async with AsyncThemeParks() as tp:
        live = await tp.entity(MAGIC_KINGDOM).live()
        for entry in sorted(live.liveData or [], key=lambda e: e.name):
            wait = current_wait_time(entry)
            if wait is None:
                print(f"{entry.name:50s}  --")
            else:
                print(f"{entry.name:50s}  {wait:>3d} min")

asyncio.run(main())
```

The sync and async clients mirror each other method-for-method. Only the
call sites need `await` and you use `async with` instead of `with`.

### Just the open rides, sorted longest-wait first

```python
from themeparks import ThemeParks, current_wait_time

with ThemeParks() as tp:
    live = tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
    waits = [
        (entry.name, current_wait_time(entry))
        for entry in live.liveData or []
    ]
    waits = [(name, w) for name, w in waits if w is not None]
    waits.sort(key=lambda pair: pair[1], reverse=True)
    for name, wait in waits:
        print(f"{wait:>3d} min  {name}")
```

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

### Reading every queue type

`current_wait_time` covers the standby-queue case. There are six queue
variants in total, and an attraction may have more than one populated at
once (e.g. STANDBY + SINGLE_RIDER + PAID_STANDBY for a Lightning Lane ride).

Each variant is exposed as an attribute on `entry.queue`. All are
`Optional` — `None` if that queue type isn't offered for the attraction:

| Attribute              | Type                  | Fields                                                                                              |
|------------------------|-----------------------|------------------------------------------------------------------------------------------------------|
| `queue.STANDBY`        | `StandbyQueue`        | `waitTime: int \| None`                                                                              |
| `queue.SINGLE_RIDER`   | `SingleRiderQueue`    | `waitTime: int \| None`                                                                              |
| `queue.PAID_STANDBY`   | `PaidStandbyQueue`    | `waitTime: int \| None`                                                                              |
| `queue.RETURN_TIME`    | `ReturnTimeQueue`     | `state`, `returnStart`, `returnEnd`                                                                  |
| `queue.PAID_RETURN_TIME` | `PaidReturnTimeQueue` | `state`, `returnStart`, `returnEnd`, `price`                                                       |
| `queue.BOARDING_GROUP` | `BoardingGroupQueue`  | `allocationStatus`, `currentGroupStart`, `currentGroupEnd`, `nextAllocationTime`, `estimatedWait` |

#### Direct access

```python
from themeparks import ThemeParks

with ThemeParks() as tp:
    live = tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
    for entry in live.liveData or []:
        if entry.queue is None:
            continue

        # Standby
        if entry.queue.STANDBY and entry.queue.STANDBY.waitTime is not None:
            print(f"{entry.name}: standby {entry.queue.STANDBY.waitTime} min")

        # Lightning Lane / paid line
        if entry.queue.PAID_RETURN_TIME:
            prt = entry.queue.PAID_RETURN_TIME
            price = prt.price.formatted if prt.price else "?"
            print(f"{entry.name}: Lightning Lane {price}, return {prt.returnStart} → {prt.returnEnd}")

        # Boarding group
        if entry.queue.BOARDING_GROUP:
            bg = entry.queue.BOARDING_GROUP
            print(
                f"{entry.name}: boarding group {bg.currentGroupStart}–{bg.currentGroupEnd}, "
                f"~{bg.estimatedWait} min wait, status {bg.allocationStatus}"
            )

        # Return-time only (no paid component)
        if entry.queue.RETURN_TIME:
            rt = entry.queue.RETURN_TIME
            print(f"{entry.name}: virtual queue {rt.returnStart} → {rt.returnEnd} ({rt.state})")
```

#### Generic iteration

If you'd rather not branch on every variant, `iter_queues(entry)` flattens
all populated queue types into one sequence of dicts keyed by `type`:

```python
from themeparks import ThemeParks, iter_queues

with ThemeParks() as tp:
    live = tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
    for entry in live.liveData or []:
        for q in iter_queues(entry):
            # q is a dict, e.g. {"type": "STANDBY", "waitTime": 35}
            #                or {"type": "PAID_RETURN_TIME", "state": "AVAILABLE", ...}
            print(entry.name, q)
```

The `type` key matches the API's variant name (`STANDBY`, `SINGLE_RIDER`,
`RETURN_TIME`, `PAID_RETURN_TIME`, `BOARDING_GROUP`, `PAID_STANDBY`). The
remaining keys are whatever fields that variant carries.

### Other helpers

`parse_api_datetime(value, timezone)` parses any API date/time string into a
timezone-aware `datetime`, honoring the entity's IANA timezone for naive
inputs.

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

## Debugging — see every HTTP request

The SDK is built on `httpx`, which has a built-in logger. Turn it on to see
every outbound request and response status:

```python
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.DEBUG)

from themeparks import ThemeParks
with ThemeParks() as tp:
    tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
```

Output:

```
INFO httpx HTTP Request: GET https://api.themeparks.wiki/v1/entity/75ea578a-adc8-4116-a54d-dccb60765ef9/live "HTTP/1.1 200 OK"
```

For raw byte-level traces (TLS handshake, header bytes, etc.), also enable
the `httpcore` logger:

```python
logging.getLogger("httpcore").setLevel(logging.DEBUG)
```

> **Note:** requests served from the in-memory cache do **not** appear in
> `httpx` logs — they're returned before the transport is touched. To see
> every call as a network round-trip while debugging, pass `cache=False`.

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
