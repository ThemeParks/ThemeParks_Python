import time
from typing import Any
from unittest.mock import patch

import pytest

from themeparks._cache import Cache, InMemoryLRUCache, ttl_for_path


def test_ttl_destinations_1h():
    assert ttl_for_path("/destinations") == 3600


def test_ttl_entity_1h():
    assert ttl_for_path("/entity/abc-123") == 3600


def test_ttl_entity_children_1h():
    assert ttl_for_path("/entity/abc-123/children") == 3600


def test_ttl_schedule_5m():
    assert ttl_for_path("/entity/abc-123/schedule") == 300
    assert ttl_for_path("/entity/abc-123/schedule/2026/4") == 300


def test_ttl_live_bypass():
    assert ttl_for_path("/entity/abc-123/live") == 0


def test_lru_get_miss():
    c = InMemoryLRUCache(max_entries=10)
    assert c.get("k") is None


def test_lru_set_and_get_within_ttl():
    c = InMemoryLRUCache(max_entries=10)
    c.set("k", {"v": 1}, 1.0)
    assert c.get("k") == {"v": 1}


def test_lru_expires():
    c = InMemoryLRUCache(max_entries=10)
    with patch("themeparks._cache.time.monotonic", side_effect=[0.0, 2.0]):
        c.set("k", 1, 1.0)
        assert c.get("k") is None


def test_lru_ttl_zero_does_not_store():
    c = InMemoryLRUCache(max_entries=10)
    c.set("k", 1, 0)
    assert c.get("k") is None


def test_lru_evicts_oldest():
    c = InMemoryLRUCache(max_entries=2)
    c.set("a", 1, 100)
    c.set("b", 2, 100)
    c.get("a")
    c.set("c", 3, 100)
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


def test_lru_delete():
    c = InMemoryLRUCache(max_entries=10)
    c.set("k", 1, 100)
    c.delete("k")
    assert c.get("k") is None


def test_protocol_accepts_user_adapter():
    store: dict[str, Any] = {}

    class MyCache:
        def get(self, key: str) -> Any:
            return store.get(key)

        def set(self, key: str, value: Any, ttl_seconds: float) -> None:
            if ttl_seconds > 0:
                store[key] = value

        def delete(self, key: str) -> None:
            store.pop(key, None)

    c: Cache = MyCache()
    c.set("x", 1, 10)
    assert c.get("x") == 1
