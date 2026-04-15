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
