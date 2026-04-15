"""Tests for the low-level raw client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from themeparks._raw import RawClient
from themeparks._transport import RetryConfig, SyncTransport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def make_raw(body) -> RawClient:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = SyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=lambda _: None,
    )
    return RawClient(transport=t)


def test_get_destinations():
    raw = make_raw(load("destinations.json"))
    res = raw.get_destinations()
    assert len(res.destinations) > 0


def test_get_entity():
    raw = make_raw(load("mk_entity.json"))
    e = raw.get_entity("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert e.id == "75ea578a-adc8-4116-a54d-dccb60765ef9"


def test_get_entity_live_with_null_queue_field():
    # This is the regression test for issues #1 and #2.
    raw = make_raw(load("null-queue-field.json"))
    live = raw.get_entity_live("75ea578a-adc8-4116-a54d-dccb60765ef9")
    assert live is not None


def test_get_entity_schedule_month_builds_correct_path():
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = req.url.path
        return httpx.Response(200, json={"schedule": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = SyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=lambda _: None,
    )
    RawClient(transport=t).get_entity_schedule_month("abc", 2026, 4)
    # Path reflects httpx.Client(base_url=...) joining with the request path.
    assert captured["url"].endswith("/entity/abc/schedule/2026/04")


def test_get_entity_schedule_month_zero_pads_single_digit_month():
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = req.url.path
        return httpx.Response(200, json={"schedule": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x/v1")
    t = SyncTransport(
        client=client,
        base_url="https://x/v1",
        user_agent="t/1",
        retry=RetryConfig(max_retries=0),
        sleep=lambda _: None,
    )
    RawClient(transport=t).get_entity_schedule_month("abc", 2026, 5)
    assert captured["url"].endswith("/entity/abc/schedule/2026/05")
