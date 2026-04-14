"""Date parsing helpers."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def parse_api_datetime(value: str, timezone: str) -> datetime:
    """Parse an API date/time string into a timezone-aware datetime.

    - Strings with a trailing ``Z`` or ``\u00b1HH:MM`` offset are parsed directly.
    - Naive strings are interpreted as local time in ``timezone``.
    """
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone))
    return dt
