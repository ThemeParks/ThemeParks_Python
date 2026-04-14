import httpx
import pytest

from themeparks import AsyncThemeParks, ThemeParks

FIXTURE = {
    "destinations": [
        {"id": "wdw", "name": "Walt Disney World Resort", "slug": "walt-disney-world", "parks": []},
        {"id": "dlp", "name": "Disneyland Paris", "slug": "disneyland-paris", "parks": []},
    ]
}


def fixture_handler(req):
    return httpx.Response(200, json=FIXTURE)


def test_list():
    tp = ThemeParks(transport=httpx.MockTransport(fixture_handler))
    res = tp.destinations.list()
    assert len(res.destinations) == 2


def test_find_by_slug():
    tp = ThemeParks(transport=httpx.MockTransport(fixture_handler))
    d = tp.destinations.find("walt-disney-world")
    assert d is not None and d.id == "wdw"


def test_find_by_name_case_insensitive():
    tp = ThemeParks(transport=httpx.MockTransport(fixture_handler))
    d = tp.destinations.find("disneyland paris")
    assert d is not None and d.id == "dlp"


def test_find_no_match():
    tp = ThemeParks(transport=httpx.MockTransport(fixture_handler))
    assert tp.destinations.find("nowhere") is None


@pytest.mark.asyncio
async def test_async_find():
    tp = AsyncThemeParks(transport=httpx.MockTransport(fixture_handler))
    d = await tp.destinations.find("walt-disney-world")
    assert d is not None and d.id == "wdw"
