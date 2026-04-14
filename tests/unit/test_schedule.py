from datetime import date

import httpx

from themeparks import ThemeParks


def _entry(d: str) -> dict:
    return {
        "date": d,
        "type": "OPERATING",
        "openingTime": f"{d}T09:00:00+00:00",
        "closingTime": f"{d}T21:00:00+00:00",
    }


def by_path(mapping):
    def h(req):
        body = mapping.get(req.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    return h


def test_schedule_month():
    tp = ThemeParks(
        transport=httpx.MockTransport(
            by_path(
                {
                    "/v1/entity/abc/schedule/2026/4": {
                        "schedule": [_entry("2026-04-01")]
                    },
                }
            )
        ),
        cache=False,
    )
    s = tp.entity("abc").schedule.month(2026, 4)
    assert len(s.schedule) == 1


def test_schedule_range_fanout_and_sort():
    tp = ThemeParks(
        transport=httpx.MockTransport(
            by_path(
                {
                    "/v1/entity/abc/schedule/2026/3": {
                        "schedule": [_entry("2026-03-31")]
                    },
                    "/v1/entity/abc/schedule/2026/4": {
                        "schedule": [_entry("2026-04-15")]
                    },
                    "/v1/entity/abc/schedule/2026/5": {
                        "schedule": [_entry("2026-05-01")]
                    },
                }
            )
        ),
        cache=False,
    )
    entries = tp.entity("abc").schedule.range(date(2026, 3, 20), date(2026, 5, 10))
    assert [e.date for e in entries] == ["2026-03-31", "2026-04-15", "2026-05-01"]
