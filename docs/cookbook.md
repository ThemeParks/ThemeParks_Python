# Cookbook

Three complete recipes you can copy, paste, and run. Each one uses real
entity IDs from the live ThemeParks.wiki API.

## Recipe 1 — Wait times in order (longest → shortest)

Pull the live data for Magic Kingdom, keep only the rides that are reporting
a wait time, and print them sorted from longest to shortest queue.

```python
from themeparks import ThemeParks, current_wait_time

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

with ThemeParks() as tp:
    live = tp.entity(MAGIC_KINGDOM).live()

waits = [
    (entry.name, current_wait_time(entry))
    for entry in live.liveData or []
]
waits = [(name, w) for name, w in waits if w is not None]
waits.sort(key=lambda pair: pair[1], reverse=True)

for name, wait in waits:
    print(f"{wait:>3d} min  {name}")
```

Sample output:

```
 90 min  Seven Dwarfs Mine Train
 75 min  TRON Lightcycle / Run
 65 min  Space Mountain
 55 min  Peter Pan's Flight
 45 min  Big Thunder Mountain Railroad
 40 min  Jungle Cruise
 35 min  Haunted Mansion
 ...
```

`current_wait_time` returns `None` for entities that don't have a STANDBY
queue right now (closed rides, shows, restaurants), which is why we filter
those out before sorting.

## Recipe 2 — Schedule for the next 7 days

Use `schedule.range(start, end)` to pull every schedule entry for a 7-day
window. A single date can have multiple entries — regular operating hours,
EXTRA_HOURS (early entry / extended evening), and TICKETED_EVENT (after-hours
events). `range()` returns them all in chronological order.

```python
from datetime import date, timedelta
from themeparks import ThemeParks

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

with ThemeParks() as tp:
    today = date.today()
    entries = tp.entity(MAGIC_KINGDOM).schedule.range(today, today + timedelta(days=7))

for entry in entries:
    if entry.type == "OPERATING":
        print(f"{entry.date}  {entry.openingTime} → {entry.closingTime}  ({entry.type})")
    else:
        print(f"{entry.date}  {entry.type}")
```

Sample output:

```
2026-04-15  2026-04-15T09:00:00-04:00 → 2026-04-15T22:00:00-04:00  (OPERATING)
2026-04-15  2026-04-15T17:00:00-04:00 → 2026-04-15T19:00:00-04:00  (TICKETED_EVENT)
2026-04-16  2026-04-16T09:00:00-04:00 → 2026-04-16T23:00:00-04:00  (OPERATING)
2026-04-17  2026-04-17T08:30:00-04:00 → 2026-04-17T09:00:00-04:00  (EXTRA_HOURS)
2026-04-17  2026-04-17T09:00:00-04:00 → 2026-04-17T22:00:00-04:00  (OPERATING)
...
```

## Recipe 3 — All entity geo-locations, grouped by type

`entity(destination_id).walk()` enumerates every descendant of a destination
(parks, attractions, restaurants, hotels, shows). Group the ones with known
coordinates by `entityType`:

> One API call. The `/children` endpoint returns the entire descendant tree
> in a single response, so even for the largest destinations this is one
> HTTP request.

```python
from collections import defaultdict
from themeparks import ThemeParks

WALT_DISNEY_WORLD = "e957da41-3552-4cf6-b636-5babc5cbc4e5"

by_type: dict[str, list[tuple[str, float, float]]] = defaultdict(list)

with ThemeParks() as tp:
    for child in tp.entity(WALT_DISNEY_WORLD).walk():
        loc = child.location
        if loc is None or loc.latitude is None or loc.longitude is None:
            continue
        by_type[child.entityType.value].append((child.name, loc.latitude, loc.longitude))

for entity_type, items in sorted(by_type.items()):
    print(f"\n{entity_type} ({len(items)})")
    print("=" * 40)
    for name, lat, lng in sorted(items):
        print(f"  {lat:>10.6f}, {lng:>11.6f}  {name}")
```

`entityType` is a `pydantic` enum — call `.value` to get the string name.

Sample output:

```
ATTRACTION (78)
========================================
   28.418250,  -81.581417  Astro Orbiter
   28.420278,  -81.583944  Big Thunder Mountain Railroad
   ...

HOTEL (33)
========================================
   28.371333,  -81.555111  Disney's All-Star Movies Resort
   ...

PARK (4)
========================================
   28.385233,  -81.563867  Disney's Animal Kingdom Theme Park
   ...

RESTAURANT (218)
========================================
   ...
```

### Async variant

The async client exposes the same `walk()` method as an async iterator. Use
`async for` inside an `async def`:

```python
import asyncio
from collections import defaultdict
from themeparks import AsyncThemeParks

WALT_DISNEY_WORLD = "e957da41-3552-4cf6-b636-5babc5cbc4e5"

async def main() -> None:
    by_type: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    async with AsyncThemeParks() as tp:
        async for child in tp.entity(WALT_DISNEY_WORLD).walk():
            loc = child.location
            if loc is None or loc.latitude is None or loc.longitude is None:
                continue
            by_type[child.entityType.value].append(
                (child.name, loc.latitude, loc.longitude)
            )
    # ... same printing as above

asyncio.run(main())
```

## Recipe 4 — See every HTTP request the SDK makes

The SDK is built on `httpx`, which has a built-in logger. Enable it before
constructing the client to see every outbound request and response status
in real time:

```python
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.DEBUG)

from themeparks import ThemeParks

with ThemeParks() as tp:
    tp.destinations.list()
    tp.entity("75ea578a-adc8-4116-a54d-dccb60765ef9").live()
```

Output:

```
INFO httpx HTTP Request: GET https://api.themeparks.wiki/v1/destinations "HTTP/1.1 200 OK"
INFO httpx HTTP Request: GET https://api.themeparks.wiki/v1/entity/75ea578a-adc8-4116-a54d-dccb60765ef9/live "HTTP/1.1 200 OK"
```

For raw byte-level traces (TLS handshake, header bytes, retries, connection
reuse) also enable the `httpcore` logger:

```python
logging.getLogger("httpcore").setLevel(logging.DEBUG)
```

Tip: requests served from the in-memory cache do **not** appear in `httpx`
logs — they are returned before the transport is touched. While debugging,
pass `cache=False` to force every call to hit the network:

```python
with ThemeParks(cache=False) as tp:
    ...
```

## Recipe 5 — Read every queue variant, not just standby

An attraction can expose more than one queue type at the same time — a
classic Disney ride often has STANDBY plus PAID_RETURN_TIME (Lightning
Lane) plus SINGLE_RIDER. The full set of variants is:

| Variant            | Use case                                     |
|--------------------|----------------------------------------------|
| `STANDBY`          | Regular wait line                            |
| `SINGLE_RIDER`     | Single-rider line wait                       |
| `PAID_STANDBY`     | Paid express line wait                       |
| `RETURN_TIME`      | Virtual queue (free)                         |
| `PAID_RETURN_TIME` | Lightning Lane / paid return time + price    |
| `BOARDING_GROUP`   | Boarding-group rides (Rise of the Resistance, TRON) |

Print all populated queue variants for every attraction at Magic Kingdom:

```python
from themeparks import ThemeParks

MAGIC_KINGDOM = "75ea578a-adc8-4116-a54d-dccb60765ef9"

with ThemeParks() as tp:
    live = tp.entity(MAGIC_KINGDOM).live()

for entry in live.liveData or []:
    if entry.queue is None:
        continue

    if entry.queue.STANDBY and entry.queue.STANDBY.waitTime is not None:
        print(f"{entry.name:40s}  STANDBY        {entry.queue.STANDBY.waitTime} min")

    if entry.queue.SINGLE_RIDER and entry.queue.SINGLE_RIDER.waitTime is not None:
        print(f"{entry.name:40s}  SINGLE_RIDER   {entry.queue.SINGLE_RIDER.waitTime} min")

    if entry.queue.PAID_RETURN_TIME:
        prt = entry.queue.PAID_RETURN_TIME
        price = prt.price.formatted if prt.price else "?"
        print(f"{entry.name:40s}  LIGHTNING LANE {price}, return {prt.returnStart} → {prt.returnEnd}")

    if entry.queue.RETURN_TIME:
        rt = entry.queue.RETURN_TIME
        print(f"{entry.name:40s}  VIRTUAL QUEUE  {rt.returnStart} → {rt.returnEnd} ({rt.state})")

    if entry.queue.BOARDING_GROUP:
        bg = entry.queue.BOARDING_GROUP
        print(
            f"{entry.name:40s}  BOARDING GROUP {bg.currentGroupStart}–{bg.currentGroupEnd}, "
            f"~{bg.estimatedWait} min, status {bg.allocationStatus}"
        )

    if entry.queue.PAID_STANDBY and entry.queue.PAID_STANDBY.waitTime is not None:
        print(f"{entry.name:40s}  PAID_STANDBY   {entry.queue.PAID_STANDBY.waitTime} min")
```

Sample output:

```
Big Thunder Mountain Railroad             STANDBY        45 min
Big Thunder Mountain Railroad             LIGHTNING LANE $15.00, return 2026-04-15T14:30:00-04:00 → 2026-04-15T15:30:00-04:00
Pirates of the Caribbean                  STANDBY        20 min
TRON Lightcycle / Run                     BOARDING GROUP 38–41, ~25 min, status AVAILABLE
TRON Lightcycle / Run                     LIGHTNING LANE $20.00, return 2026-04-15T15:00:00-04:00 → 2026-04-15T16:00:00-04:00
...
```

### Generic alternative

If branching on every variant is too verbose, `iter_queues(entry)` flattens
all populated variants into one sequence of dicts:

```python
from themeparks import ThemeParks, iter_queues

with ThemeParks() as tp:
    live = tp.entity(MAGIC_KINGDOM).live()
    for entry in live.liveData or []:
        for q in iter_queues(entry):
            # q is e.g. {"type": "STANDBY", "waitTime": 45}
            #         or {"type": "PAID_RETURN_TIME", "state": "AVAILABLE", "price": {...}, ...}
            print(entry.name, q["type"], q)
```
