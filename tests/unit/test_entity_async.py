import asyncio
import time
from datetime import date

import httpx

from themeparks import AsyncThemeParks


def sequential_handler(responses):
    idx = {"i": 0}

    def h(req):
        r = responses[idx["i"]]
        idx["i"] += 1
        return httpx.Response(200, json=r)

    return h


def by_path(mapping):
    def h(req):
        body = mapping.get(req.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    return h


def _entry(d: str) -> dict:
    return {
        "date": d,
        "type": "OPERATING",
        "openingTime": f"{d}T09:00:00+00:00",
        "closingTime": f"{d}T21:00:00+00:00",
    }


async def test_async_get_calls_entity_path():
    body = {
        "id": "abc",
        "name": "X",
        "entityType": "PARK",
        "timezone": "UTC",
    }
    tp = AsyncThemeParks(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
        cache=False,
    )
    e = await tp.entity("abc").get()
    assert e.id == "abc"


async def test_async_walk_bfs_and_dedupes():
    responses = [
        {
            "id": "root",
            "children": [
                {"id": "a", "name": "A", "entityType": "PARK"},
                {"id": "b", "name": "B", "entityType": "PARK"},
            ],
        },
        {
            "id": "a",
            "children": [{"id": "c", "name": "C", "entityType": "ATTRACTION"}],
        },
        {
            "id": "b",
            "children": [{"id": "c", "name": "C", "entityType": "ATTRACTION"}],
        },
        {"id": "c", "children": []},
    ]
    tp = AsyncThemeParks(transport=httpx.MockTransport(sequential_handler(responses)), cache=False)
    ids = [c.id async for c in tp.entity("root").walk()]
    assert ids == ["a", "b", "c"]


async def test_async_schedule_month():
    tp = AsyncThemeParks(
        transport=httpx.MockTransport(
            by_path(
                {
                    "/v1/entity/abc/schedule/2026/04": {"schedule": [_entry("2026-04-01")]},
                }
            )
        ),
        cache=False,
    )
    s = await tp.entity("abc").schedule.month(2026, 4)
    assert len(s.schedule) == 1


async def test_async_schedule_range_fanout_and_sort():
    tp = AsyncThemeParks(
        transport=httpx.MockTransport(
            by_path(
                {
                    "/v1/entity/abc/schedule/2026/03": {"schedule": [_entry("2026-03-31")]},
                    "/v1/entity/abc/schedule/2026/04": {"schedule": [_entry("2026-04-15")]},
                    "/v1/entity/abc/schedule/2026/05": {"schedule": [_entry("2026-05-01")]},
                }
            )
        ),
        cache=False,
    )
    entries = await tp.entity("abc").schedule.range(date(2026, 3, 20), date(2026, 5, 10))
    assert [e.date for e in entries] == ["2026-03-31", "2026-04-15", "2026-05-01"]


async def test_async_schedule_range_empty_when_end_before_start():
    tp = AsyncThemeParks(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"schedule": []})),
        cache=False,
    )
    assert await tp.entity("abc").schedule.range(date(2026, 5, 1), date(2026, 3, 1)) == []


async def test_async_walk_fetches_concurrently():
    """Children at a single BFS level should be fetched in parallel.

    Builds a tree with a root that has N children (all leaves). With sequential
    fetching this takes N * delay; with parallel fetching it should take
    roughly 1 * delay. We also count the peak number of concurrently in-flight
    requests and assert it exceeded 1, which is immune to timing jitter.
    """
    delay = 0.05
    n_children = 6
    in_flight = 0
    peak_in_flight = 0
    lock = asyncio.Lock()

    async def handler_inner(req: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak_in_flight
        async with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        try:
            await asyncio.sleep(delay)
        finally:
            async with lock:
                in_flight -= 1
        path = req.url.path
        if path.endswith("/root/children"):
            return httpx.Response(
                200,
                json={
                    "id": "root",
                    "children": [
                        {"id": f"c{i}", "name": f"C{i}", "entityType": "ATTRACTION"}
                        for i in range(n_children)
                    ],
                },
            )
        # Leaves: empty children list
        return httpx.Response(200, json={"id": path.rsplit("/", 2)[1], "children": []})

    tp = AsyncThemeParks(transport=httpx.MockTransport(handler_inner), cache=False)
    start = time.monotonic()
    ids = [c.id async for c in tp.entity("root").walk()]
    elapsed = time.monotonic() - start

    assert sorted(ids) == [f"c{i}" for i in range(n_children)]
    # Parallelism indicator: at some point more than one fetch was in flight.
    assert peak_in_flight > 1, f"expected >1 concurrent fetch, got {peak_in_flight}"
    # Timing sanity: sequential would be >= (1 + n_children) * delay.
    # Parallel (level 0 serial, level 1 parallel) should be ~2 * delay.
    # Allow generous margin but still well under the sequential bound.
    sequential_bound = (1 + n_children) * delay
    assert elapsed < sequential_bound * 0.75, (
        f"walk took {elapsed:.3f}s, expected well under {sequential_bound:.3f}s"
    )


async def test_async_walk_respects_concurrency_cap():
    """The ``concurrency`` param should cap parallel in-flight fetches."""
    in_flight = 0
    peak_in_flight = 0
    lock = asyncio.Lock()

    async def handler_inner(req: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak_in_flight
        async with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            async with lock:
                in_flight -= 1
        path = req.url.path
        if path.endswith("/root/children"):
            return httpx.Response(
                200,
                json={
                    "id": "root",
                    "children": [
                        {"id": f"c{i}", "name": f"C{i}", "entityType": "ATTRACTION"}
                        for i in range(10)
                    ],
                },
            )
        return httpx.Response(200, json={"id": path.rsplit("/", 2)[1], "children": []})

    tp = AsyncThemeParks(transport=httpx.MockTransport(handler_inner), cache=False)
    ids = [c.id async for c in tp.entity("root").walk(concurrency=2)]
    assert len(ids) == 10
    assert peak_in_flight <= 2


async def test_async_entity_children_and_live():
    def handler(req):
        if req.url.path.endswith("/children"):
            return httpx.Response(
                200,
                json={"id": "abc", "children": [{"id": "x", "name": "X", "entityType": "PARK"}]},
            )
        if req.url.path.endswith("/live"):
            return httpx.Response(200, json={"id": "abc", "name": "X", "liveData": []})
        if req.url.path.endswith("/schedule"):
            return httpx.Response(200, json={"schedule": []})
        return httpx.Response(404)

    tp = AsyncThemeParks(transport=httpx.MockTransport(handler), cache=False)
    h = tp.entity("abc")
    ch = await h.children()
    assert ch.children[0].id == "x"
    live = await h.live()
    assert live.id == "abc"
    sched = await h.schedule.upcoming()
    assert sched.schedule == []
