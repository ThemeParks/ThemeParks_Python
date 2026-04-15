# Quickstart

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

## Just the open rides, sorted longest-wait first

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
