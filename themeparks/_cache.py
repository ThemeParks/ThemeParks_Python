"""Cache protocol, in-memory LRU default, and per-endpoint TTL table."""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CacheConfig:
    max_entries: int = 500


HOUR = 3600
FIVE_MINUTES = 300

_LIVE_RE = re.compile(r"^/entity/[^/]+/live$")
_SCHEDULE_RE = re.compile(r"^/entity/[^/]+/schedule(?:/\d+/\d+)?$")
_CHILDREN_RE = re.compile(r"^/entity/[^/]+/children$")
_ENTITY_RE = re.compile(r"^/entity/[^/]+$")


def ttl_for_path(path: str) -> float:
    """Return the default TTL (seconds) for an API path. 0 means bypass."""
    if _LIVE_RE.match(path):
        return 0
    if _SCHEDULE_RE.match(path):
        return FIVE_MINUTES
    if _CHILDREN_RE.match(path):
        return HOUR
    if _ENTITY_RE.match(path):
        return HOUR
    if path == "/destinations":
        return HOUR
    return 0


@runtime_checkable
class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemoryLRUCache:
    """Bounded, TTL-aware in-memory LRU cache."""

    def __init__(self, *, max_entries: int = 500) -> None:
        self._max = max_entries
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        if key in self._data:
            self._data.pop(key)
        self._data[key] = (value, time.monotonic() + ttl_seconds)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
