import httpx
import pytest

from themeparks import AsyncThemeParks, ThemeParks


def _handler(counter, body):
    def h(req):
        counter["n"] += 1
        return httpx.Response(200, json=body)

    return h


def test_default_base_url_and_user_agent():
    counter = {"n": 0}
    body = {"destinations": []}
    tp = ThemeParks(transport=httpx.MockTransport(_handler(counter, body)))
    tp.raw.get_destinations()
    assert counter["n"] == 1


def test_cache_on_by_default_for_destinations():
    counter = {"n": 0}
    body = {"destinations": []}
    tp = ThemeParks(transport=httpx.MockTransport(_handler(counter, body)))
    tp.raw.get_destinations()
    tp.raw.get_destinations()
    assert counter["n"] == 1


def test_cache_bypasses_live():
    counter = {"n": 0}
    body = {"id": "abc", "name": "X", "liveData": []}
    tp = ThemeParks(transport=httpx.MockTransport(_handler(counter, body)))
    tp.raw.get_entity_live("abc")
    tp.raw.get_entity_live("abc")
    assert counter["n"] == 2


def test_cache_false_disables():
    counter = {"n": 0}
    body = {"destinations": []}
    tp = ThemeParks(transport=httpx.MockTransport(_handler(counter, body)), cache=False)
    tp.raw.get_destinations()
    tp.raw.get_destinations()
    assert counter["n"] == 2


def test_context_manager_closes_client():
    with ThemeParks() as tp:
        assert tp is not None


@pytest.mark.asyncio
async def test_async_context_manager():
    async with AsyncThemeParks() as tp:
        assert tp is not None
