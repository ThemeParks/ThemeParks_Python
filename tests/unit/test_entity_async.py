from datetime import date

import httpx

from themeparks import AsyncThemeParks


def sequential_handler(responses):
    idx = {"i": 0}

    def h(req):
        r = responses[idx["i"]]
        idx["i"] += 1
        h.call_count = idx["i"]
        return httpx.Response(200, json=r)

    h.call_count = 0
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


async def test_async_walk_yields_all_descendants_from_single_call():
    fn = sequential_handler(
        [
            {
                "id": "root",
                "entityType": "DESTINATION",
                "children": [
                    {"id": "a", "name": "A", "entityType": "PARK"},
                    {"id": "b", "name": "B", "entityType": "PARK"},
                    {"id": "c", "name": "C", "entityType": "ATTRACTION"},
                ],
            },
        ]
    )
    tp = AsyncThemeParks(transport=httpx.MockTransport(fn), cache=False)
    ids = [c.id async for c in tp.entity("root").walk()]
    assert ids == ["a", "b", "c"]
    assert fn.call_count == 1


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
