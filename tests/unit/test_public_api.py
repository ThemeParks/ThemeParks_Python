"""Smoke tests asserting the public API surface is stable."""

import themeparks
from themeparks import (
    AsyncThemeParks,
    ThemeParks,
    current_wait_time,
    iter_queues,
    parse_api_datetime,
)


def test_top_level_imports_resolve():
    # Quick sanity check that these are actually callable/classes
    assert callable(ThemeParks)
    assert callable(AsyncThemeParks)
    assert callable(current_wait_time)
    assert callable(iter_queues)
    assert callable(parse_api_datetime)


def test_all_export_list_matches_imports():
    for name in themeparks.__all__:
        assert hasattr(themeparks, name), f"{name} in __all__ but not importable"
