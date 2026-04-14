"""Entity handle stubs — filled in by Task 11."""
from __future__ import annotations

from typing import Any


class EntityHandle:
    # Stub — filled in by Task 11
    def __init__(self, *, raw: Any, entity_id: str) -> None:
        self._raw = raw
        self._entity_id = entity_id


class AsyncEntityHandle:
    # Stub — filled in by Task 11
    def __init__(self, *, raw: Any, entity_id: str) -> None:
        self._raw = raw
        self._entity_id = entity_id
