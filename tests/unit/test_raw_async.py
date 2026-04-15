"""Tests for the async low-level raw client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from themeparks._raw import AsyncRawClient
from themeparks._transport import AsyncTransport, RetryConfig

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


async def _noop_sleep(_):
    return None


def make_async_raw(body) -> AsyncRawClient:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = AsyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=_noop_sleep,
    )
    return AsyncRawClient(transport=t)


async def test_async_get_destinations():
    raw = make_async_raw(load("destinations.json"))
    res = await raw.get_destinations()
    assert len(res.destinations) > 0


async def test_async_get_entity_live_with_null_queue_field():
    raw = make_async_raw(load("null-queue-field.json"))
    live = await raw.get_entity_live("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert live is not None


async def test_async_get_entity_schedule_month():
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = req.url.path
        return httpx.Response(200, json={"schedule": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = AsyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=_noop_sleep,
    )
    await AsyncRawClient(transport=t).get_entity_schedule_month("abc", 2026, 4)
    assert captured["url"].endswith("/entity/abc/schedule/2026/04")


async def test_async_get_entity_schedule_month_zero_pads_single_digit_month():
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = req.url.path
        return httpx.Response(200, json={"schedule": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = AsyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=_noop_sleep,
    )
    await AsyncRawClient(transport=t).get_entity_schedule_month("abc", 2026, 5)
    assert captured["url"].endswith("/entity/abc/schedule/2026/05")


async def test_async_get_entity():
    raw = make_async_raw(load("mk_entity.json"))
    e = await raw.get_entity("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert e.id == "75ea578a-adc8-4116-a54d-dccb60765ef9"


async def test_async_get_entity_children_and_schedule():
    raw = make_async_raw(load("mk_children.json"))
    ch = await raw.get_entity_children("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert ch is not None
    raw2 = make_async_raw(load("mk_schedule.json"))
    sched = await raw2.get_entity_schedule("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert sched is not None
