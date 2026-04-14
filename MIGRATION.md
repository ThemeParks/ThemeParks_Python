# Migrating from v1 to v2

v2 is a full rewrite. The package name on PyPI is still `themeparks`, but the
import surface has changed. This guide walks through the common patterns.

If you are blocked on a migration detail not covered here, please open an
issue: https://github.com/ThemeParks/ThemeParks_Python/issues

## TL;DR

- Replace `from openapi_client...` imports with `from themeparks import ThemeParks`.
- Wrap usage in a `with ThemeParks() as tp:` block (or use `AsyncThemeParks`).
- Access response fields as attributes on pydantic models, not dict keys.
- `openapi_client.ApiException` is now `themeparks.APIError` (with typed subclasses).
- Null `waitTime` and other queue fields no longer raise — the fields are
  `Optional` and deserialize correctly.

## Imports and client setup

### v1

```python
from openapi_client import ApiClient, Configuration
from openapi_client.api.destinations_api import DestinationsApi
from openapi_client.api.entity_api import EntityApi

configuration = Configuration(host="https://api.themeparks.wiki/v1")
api_client = ApiClient(configuration)
destinations_api = DestinationsApi(api_client)
entity_api = EntityApi(api_client)
```

### v2

```python
from themeparks import ThemeParks

with ThemeParks() as tp:
    ...  # tp.destinations, tp.entity(...), tp.raw.*
```

One client, one context manager, both sync and async variants.

## Listing destinations

### v1

```python
response = destinations_api.get_destinations()
for d in response["destinations"]:
    print(d["id"], d["name"])
```

### v2

```python
# Ergonomic
resp = tp.destinations.list()
for d in resp.destinations or []:
    print(d.id, d.name)

# Or raw (same return type)
resp = tp.raw.get_destinations()
```

Note the switch from `response["destinations"]` to attribute access
(`resp.destinations`). All response types are pydantic models.

## Fetching an entity

### v1

```python
entity = entity_api.get_entity(entity_id)
print(entity["name"], entity["entityType"])
```

### v2

```python
entity = tp.entity(entity_id).get()         # ergonomic
entity = tp.raw.get_entity(entity_id)       # raw
print(entity.name, entity.entityType)
```

## Live wait times (the main v1 bug)

In v1, calling `get_entity_live_data` on a park where any queue had a null
`waitTime` raised `ApiTypeError` (see issues #1 and #2). The typical workaround
was patching the generated models or catching and ignoring the error. In v2
this Just Works — `waitTime` is `Optional[float]` and null values deserialize
to `None`:

### v1 (broken)

```python
try:
    live = entity_api.get_live_data(park_id)
except openapi_client.exceptions.ApiTypeError:
    # fall back to raw urllib3, re-parse by hand, etc.
    ...
```

### v2 (works)

```python
live = tp.entity(park_id).live()
for item in live.liveData or []:
    standby = item.queue.STANDBY if item.queue else None
    wait = standby.waitTime if standby else None
    if wait is None:
        print(f"{item.name}: closed / no data")
    else:
        print(f"{item.name}: {int(wait)} min")
```

No workaround needed.

## Error handling

### v1

```python
from openapi_client.exceptions import ApiException

try:
    entity_api.get_entity(entity_id)
except ApiException as exc:
    print(exc.status, exc.body)
```

### v2

```python
from themeparks import APIError, RateLimitError, NetworkError, TimeoutError

try:
    tp.entity(entity_id).get()
except RateLimitError as exc:
    # 429: exc.retry_after is seconds (from Retry-After header), if present
    ...
except APIError as exc:
    # any non-2xx
    print(exc.status, exc.url, exc.body)
except (NetworkError, TimeoutError):
    ...
```

`APIError`, `RateLimitError`, `NetworkError`, and `TimeoutError` all inherit
from `ThemeParksError` if you want a single catch-all.

## Async

v1 had no async client. In v2:

```python
import asyncio
from themeparks import AsyncThemeParks

async def main():
    async with AsyncThemeParks() as tp:
        resp = await tp.destinations.list()
        for d in resp.destinations or []:
            print(d.name)

asyncio.run(main())
```

The async surface mirrors the sync surface method-for-method.

## New in v2 (not in v1)

- `tp.entity(id).walk()` — breadth-first iterator over every descendant.
- `tp.entity(id).schedule.range(start, end)` — stitches monthly schedule
  responses into a single sorted, filtered list.
- `tp.destinations.find(query)` — case-insensitive lookup by slug or name.
- Default-on response caching with per-endpoint TTLs (see README).
- Automatic 429 `Retry-After` handling.
