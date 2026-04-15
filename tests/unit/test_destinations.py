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


def _client() -> ThemeParks:
    return ThemeParks(transport=httpx.MockTransport(fixture_handler))


def _async_client() -> AsyncThemeParks:
    return AsyncThemeParks(transport=httpx.MockTransport(fixture_handler))


def test_list():
    tp = _client()
    res = tp.destinations.list()
    assert len(res.destinations) == 2


def test_find_by_slug_exact():
    tp = _client()
    d = tp.destinations.find("walt-disney-world")
    assert d is not None and d.id == "wdw"


def test_find_by_name_case_insensitive():
    tp = _client()
    d = tp.destinations.find("disneyland paris")
    assert d is not None and d.id == "dlp"


def test_find_loose_match_no_punctuation():
    tp = _client()
    d = tp.destinations.find("waltdisneyworld")
    assert d is not None and d.id == "wdw"


def test_find_loose_match_extra_suffix_in_name():
    # The README example: user types 'waltdisneyworldresort', which matches
    # against the normalized name 'waltdisneyworldresort'.
    tp = _client()
    d = tp.destinations.find("waltdisneyworldresort")
    assert d is not None and d.id == "wdw"


def test_find_returns_first_on_multiple_matches():
    # 'disney' is a substring of both destinations (one via name, one via slug).
    # Document that `find` returns the first match in list order.
    tp = _client()
    d = tp.destinations.find("disney")
    assert d is not None and d.id == "wdw"


def test_find_no_match_still_returns_none():
    tp = _client()
    assert tp.destinations.find("nowhere") is None


def test_find_empty_query_returns_none():
    tp = _client()
    assert tp.destinations.find("   ") is None


@pytest.mark.asyncio
async def test_async_find_by_slug_exact():
    tp = _async_client()
    d = await tp.destinations.find("walt-disney-world")
    assert d is not None and d.id == "wdw"


@pytest.mark.asyncio
async def test_async_find_by_name_case_insensitive():
    tp = _async_client()
    d = await tp.destinations.find("disneyland paris")
    assert d is not None and d.id == "dlp"


@pytest.mark.asyncio
async def test_async_find_loose_match_no_punctuation():
    tp = _async_client()
    d = await tp.destinations.find("waltdisneyworldresort")
    assert d is not None and d.id == "wdw"


@pytest.mark.asyncio
async def test_async_find_returns_first_on_multiple_matches():
    tp = _async_client()
    d = await tp.destinations.find("disney")
    assert d is not None and d.id == "wdw"


@pytest.mark.asyncio
async def test_async_find_no_match_still_returns_none():
    tp = _async_client()
    assert await tp.destinations.find("nowhere") is None
