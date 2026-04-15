from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from themeparks._ergonomic.dates import parse_api_datetime


def test_parse_iso_with_offset():
    d = parse_api_datetime("2026-04-14T09:00:00-04:00", "America/New_York")
    assert d == datetime(2026, 4, 14, 13, 0, tzinfo=ZoneInfo("UTC"))


def test_parse_iso_with_z():
    d = parse_api_datetime("2026-04-14T13:00:00Z", "America/New_York")
    assert d == datetime(2026, 4, 14, 13, 0, tzinfo=ZoneInfo("UTC"))


def test_parse_naive_as_local_to_timezone():
    d = parse_api_datetime("2026-04-14T09:00:00", "America/New_York")
    assert d.astimezone(ZoneInfo("UTC")) == datetime(2026, 4, 14, 13, 0, tzinfo=ZoneInfo("UTC"))


def test_invalid_raises():
    with pytest.raises(ValueError):
        parse_api_datetime("not a date", "UTC")
