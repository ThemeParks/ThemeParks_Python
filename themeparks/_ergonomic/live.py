"""Helpers for narrowing live queue data."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from themeparks._generated.models import EntityLiveData

# Python attribute -> API-facing type name for each LiveQueue variant.
# The attribute names here match the API field names verbatim thanks to the
# queue-variant class renames applied by ``scripts/regenerate.py``.
_QUEUE_TYPES: dict[str, str] = {
    "STANDBY": "STANDBY",
    "SINGLE_RIDER": "SINGLE_RIDER",
    "RETURN_TIME": "RETURN_TIME",
    "PAID_RETURN_TIME": "PAID_RETURN_TIME",
    "BOARDING_GROUP": "BOARDING_GROUP",
    "PAID_STANDBY": "PAID_STANDBY",
}


def current_wait_time(entry: EntityLiveData) -> int | None:
    """Return the STANDBY queue's waitTime as an int, or None if unavailable."""
    queue = entry.queue
    if queue is None:
        return None
    standby = queue.STANDBY
    if standby is None or standby.waitTime is None:
        return None
    return int(standby.waitTime)


def iter_queues(entry: EntityLiveData) -> Iterator[dict[str, Any]]:
    """Yield one dict per non-null queue variant, tagged with its API ``type``."""
    queue = entry.queue
    if queue is None:
        return
    for attr, type_name in _QUEUE_TYPES.items():
        payload = getattr(queue, attr, None)
        if payload is None:
            continue
        yield {"type": type_name, **payload.model_dump()}
