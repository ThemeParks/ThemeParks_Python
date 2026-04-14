"""Helpers for narrowing live queue data."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from themeparks._generated.models import EntityLiveData

# Map internal pydantic attribute names to API-facing alias names.
# The LiveQueue model's STANDBY field is renamed to STANDBY_1 by the generator
# because it collides with the STANDBY class name; we re-map back here.
_QUEUE_ALIAS: dict[str, str] = {
    "STANDBY_1": "STANDBY",
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
    standby = queue.STANDBY_1
    if standby is None or standby.waitTime is None:
        return None
    return int(standby.waitTime)


def iter_queues(entry: EntityLiveData) -> Iterator[dict[str, Any]]:
    """Yield one dict per non-null queue variant, tagged with its API ``type``."""
    queue = entry.queue
    if queue is None:
        return
    for attr, alias in _QUEUE_ALIAS.items():
        payload = getattr(queue, attr, None)
        if payload is None:
            continue
        yield {"type": alias, **payload.model_dump()}
