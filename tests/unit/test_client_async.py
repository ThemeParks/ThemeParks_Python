import httpx

from themeparks import AsyncThemeParks


def _handler(counter, body):
    def h(req):
        counter["n"] += 1
        return httpx.Response(200, json=body)

    return h


async def test_async_raw_works_through_context_manager():
    counter = {"n": 0}
    body = {"destinations": []}
    async with AsyncThemeParks(
        transport=httpx.MockTransport(_handler(counter, body))
    ) as tp:
        res = await tp.raw.get_destinations()
        assert res.destinations == []
    assert counter["n"] == 1


async def test_async_cache_on_by_default_for_destinations():
    counter = {"n": 0}
    body = {"destinations": []}
    tp = AsyncThemeParks(transport=httpx.MockTransport(_handler(counter, body)))
    await tp.raw.get_destinations()
    await tp.raw.get_destinations()
    assert counter["n"] == 1


async def test_async_cache_bypasses_live():
    counter = {"n": 0}
    body = {"id": "abc", "name": "X", "liveData": []}
    tp = AsyncThemeParks(transport=httpx.MockTransport(_handler(counter, body)))
    await tp.raw.get_entity_live("abc")
    await tp.raw.get_entity_live("abc")
    assert counter["n"] == 2


async def test_async_cache_false_disables():
    counter = {"n": 0}
    body = {"destinations": []}
    tp = AsyncThemeParks(
        transport=httpx.MockTransport(_handler(counter, body)), cache=False
    )
    await tp.raw.get_destinations()
    await tp.raw.get_destinations()
    assert counter["n"] == 2
