"""History helpers: paging, park flattening, and the hourly budget.

The point of this layer is that a caller does not write the loop. These tests
are about the loop being right, because every customer who buys history would
otherwise write it themselves and most would write it the way our own monitor
did: treating a 429 as no data.
"""

from datetime import date as _d

import httpx
import pytest

from themeparks import BudgetExhaustedError, RetryConfig, ThemeParks
from themeparks._errors import RateLimitError


def client(handler):
    return ThemeParks(transport=httpx.MockTransport(handler), cache=False)


# Shaped from the generated models' required fields rather than trimmed by
# hand: a thin fixture passes for the wrong reason, and the published contract
# is the thing these helpers have to survive.
def day(d):
    return {
        "date": d,
        "firstOperatingAt": f"{d}T09:00:00Z",
        "lastClosedAt": f"{d}T22:00:00Z",
        "operatingMinutes": 780,
        "downMinutes": 0,
        "changes": 42,
    }


def daily_entity(rows, nxt=None):
    return {
        "id": "ride-1",
        "name": "Space Mountain",
        "entityType": "ATTRACTION",
        "parentId": "park-1",
        "destinationId": "dest-1",
        "timezone": "America/New_York",
        "range": {"from": "2026-09-01", "to": "2026-09-02"},
        "coverage": {"firstRecordedAt": "2021-07-03"},
        "days": rows,
        "next": nxt,
    }


def park_envelope(entities, key, nxt=None):
    return {
        "id": "park-1",
        "name": "Magic Kingdom Park",
        "entityType": "PARK",
        "parentId": "dest-1",
        "destinationId": "dest-1",
        "timezone": "America/New_York",
        "range": {"from": "2026-09-01", "to": "2026-09-01"},
        "entities": entities,
        "next": nxt,
    }


class TestPaging:
    def test_follows_next_until_the_server_stops_offering_one(self):
        pages = [
            daily_entity(
                [day("2026-09-01")],
                nxt="https://api.themeparks.wiki/v1/entity/ride-1/history/daily?from=2026-09-02",
            ),
            daily_entity([day("2026-09-02")], nxt=None),
        ]
        seen = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, json=pages[len(seen) - 1])

        rows = list(client(handler).entity("ride-1").history.days("2026-09-01", "2026-09-02"))
        # Real date objects, not strings: the generated models parse the
        # park-local day for you, which is the whole reason to use the library
        # over raw JSON.
        assert [r.date for _, r in rows] == [_d(2026, 9, 1), _d(2026, 9, 2)]
        assert len(seen) == 2

    def test_follows_the_url_the_server_gave_rather_than_rebuilding_it(self):
        # Re-deriving the next page from its parts is how a client drifts from
        # the server's own idea of where the next page starts.
        nxt = "https://api.themeparks.wiki/v1/entity/ride-1/history/daily?from=2026-09-02&to=2026-09-30"
        pages = [daily_entity([day("2026-09-01")], nxt=nxt), daily_entity([day("2026-09-02")])]
        seen = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, json=pages[len(seen) - 1])

        list(client(handler).entity("ride-1").history.days())
        assert seen[1] == nxt

    def test_one_page_makes_one_request(self):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            return httpx.Response(200, json=daily_entity([day("2026-09-01")]))

        list(client(handler).entity("ride-1").history.days())
        assert calls["n"] == 1


class TestParkShape:
    def test_a_park_envelope_flattens_to_the_same_stream_as_an_entity(self):
        # Both history endpoints answer a whole park in one request, and the
        # response carries `entities` instead of `days`. A caller should not
        # have to branch on that: asking a park is the CHEAP path (124 requests
        # against 15,128 for one resort's five years) and the shape difference
        # must not be the reason someone avoids it.
        park = park_envelope(
            [
                {
                    "id": "ride-a",
                    "name": "A",
                    "entityType": "ATTRACTION",
                    "coverage": {"firstRecordedAt": "2021-07-03"},
                    "days": [day("2026-09-01")],
                },
                {
                    "id": "ride-b",
                    "name": "B",
                    "entityType": "ATTRACTION",
                    "coverage": {"firstRecordedAt": "2021-07-03"},
                    "days": [day("2026-09-01")],
                },
            ],
            "days",
        )
        rows = list(
            client(lambda r: httpx.Response(200, json=park)).entity("park-1").history.days()
        )
        assert [entity_id for entity_id, _ in rows] == ["ride-a", "ride-b"]

    def test_changes_flattens_a_park_the_same_way(self):
        park = park_envelope(
            [
                {
                    "id": "ride-a",
                    "name": "A",
                    "entityType": "ATTRACTION",
                    "coverage": {"firstRecordedAt": "2021-07-03"},
                    "opening": {"time": "2026-09-01T00:00:00Z", "status": "CLOSED"},
                    "history": [
                        {
                            "time": "2026-09-01T12:00:00Z",
                            "changed": ["status"],
                            "status": "OPERATING",
                        }
                    ],
                },
            ],
            "history",
        )
        rows = list(
            client(lambda r: httpx.Response(200, json=park))
            .entity("park-1")
            .history.changes(date="2026-09-01")
        )
        assert rows[0][0] == "ride-a"
        assert rows[0][1].status == "OPERATING"


class TestBudget:
    def _limited(self, retry_after):
        def handler(req):
            return httpx.Response(
                429,
                headers={"retry-after": str(retry_after)},
                json={
                    "error": {
                        "type": "HISTORY_RATE_LIMITED",
                        "message": "Requests without a key get 60 history requests an hour.",
                        "retryAfter": retry_after,
                    }
                },
            )

        return handler

    def test_a_long_wait_raises_instead_of_blocking(self):
        # The history budget is hourly, so a spent one can be 50 minutes from
        # resetting. Sleeping through that is indistinguishable from a hung
        # process, and a backfill would rather checkpoint and come back.
        tp = ThemeParks(
            transport=httpx.MockTransport(self._limited(2900)),
            cache=False,
            retry=RetryConfig(max_retries=0),
        )
        with pytest.raises(BudgetExhaustedError) as exc:
            list(tp.entity("ride-1").history.days())
        assert exc.value.retry_after == 2900
        assert "Checkpoint and resume" in str(exc.value)

    def test_the_error_is_still_a_rate_limit_error(self):
        # Anyone already catching RateLimitError keeps working.
        tp = ThemeParks(
            transport=httpx.MockTransport(self._limited(2900)),
            cache=False,
            retry=RetryConfig(max_retries=0),
        )
        with pytest.raises(RateLimitError):
            list(tp.entity("ride-1").history.days())

    def test_a_short_wait_is_left_to_the_transport(self):
        # A brief limit is the transport's job; it already honours Retry-After.
        tp = ThemeParks(
            transport=httpx.MockTransport(self._limited(5)),
            cache=False,
            retry=RetryConfig(max_retries=0),
        )
        with pytest.raises(RateLimitError) as exc:
            list(tp.entity("ride-1").history.days())
        assert not isinstance(exc.value, BudgetExhaustedError)


class TestArguments:
    def test_date_and_range_together_is_refused_before_the_request(self):
        # The API answers 400 INVALID_DATE for this pair, so there is no reason
        # to spend a request finding out.
        tp = client(lambda r: httpx.Response(200, json=daily_entity([])))
        with pytest.raises(ValueError, match="either date, or from/to"):
            list(tp.entity("ride-1").history.changes("2026-09-01", start="2026-09-01"))

    def test_accepts_date_objects_as_well_as_strings(self):
        seen = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, json=daily_entity([]))

        list(client(handler).entity("ride-1").history.days(_d(2026, 9, 1), _d(2026, 9, 30)))
        assert "from=2026-09-01" in seen[0]
        assert "to=2026-09-30" in seen[0]


class TestCoverage:
    def test_coverage_returns_what_exists(self):
        body = {
            "id": "ride-1",
            "name": "A",
            "entityType": "ATTRACTION",
            "parentId": "park-1",
            "destinationId": "dest-1",
            "timezone": "UTC",
            "firstRecordedAt": "2021-07-03",
            "lastRecordedAt": "2026-09-20",
            "retrievableThrough": "2026-09-20",
            "kinds": {},
        }
        doc = client(lambda r: httpx.Response(200, json=body)).entity("ride-1").history.coverage()
        assert doc.firstRecordedAt == _d(2021, 7, 3)


class TestApiKey:
    """The library could not send a key at all until 2026-09-23.

    That capped the official SDK at the anonymous window, seven days of
    history and the unauthenticated rate limit, so a paying customer had to
    drop to raw HTTP to use what they had bought. These pin it shut.
    """

    def _capture(self, seen):
        def handler(req):
            seen.append(dict(req.headers))
            return httpx.Response(200, json=daily_entity([]))

        return handler

    def test_sends_the_key_as_x_api_key(self):
        seen = []
        tp = ThemeParks(
            transport=httpx.MockTransport(self._capture(seen)), cache=False, api_key="tpw_example"
        )
        list(tp.entity("ride-1").history.days())
        assert seen[0]["x-api-key"] == "tpw_example"

    def test_sends_no_key_header_when_none_was_given(self):
        seen = []
        tp = ThemeParks(transport=httpx.MockTransport(self._capture(seen)), cache=False)
        list(tp.entity("ride-1").history.days())
        assert "x-api-key" not in seen[0]

    def test_an_empty_key_is_treated_as_no_key(self):
        # An unset environment variable arrives as "" far more often than as
        # None, and sending an empty key is a 401 rather than an anonymous
        # request.
        seen = []
        tp = ThemeParks(transport=httpx.MockTransport(self._capture(seen)), cache=False, api_key="")
        list(tp.entity("ride-1").history.days())
        assert "x-api-key" not in seen[0]

    def test_the_key_reaches_every_endpoint_not_just_history(self):
        seen = []

        def handler(req):
            seen.append(dict(req.headers))
            return httpx.Response(200, json={"destinations": []})

        tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False, api_key="tpw_example")
        tp.destinations.list()
        assert seen[0]["x-api-key"] == "tpw_example"


PARK_COVERAGE = {
    "id": "park-1",
    "name": "Magic Kingdom Park",
    "entityType": "PARK",
    "parentId": "dest-1",
    "destinationId": "dest-1",
    "timezone": "America/New_York",
    "summary": {
        "entitiesWithData": 87,
        "archiveFrom": "2021-07-03",
        "recordedTo": "2026-09-22",
        "retrievableThrough": "2026-08-23",
        "measuredOn": "2026-09-23",
    },
    "fields": {},
    "entities": [],
}

ENTITY_COVERAGE = {
    "id": "ride-1",
    "name": "Space Mountain",
    "entityType": "ATTRACTION",
    "parentId": "park-1",
    "destinationId": "dest-1",
    "timezone": "America/New_York",
    "firstRecordedAt": "2021-07-03",
    "lastRecordedAt": "2026-09-22",
    "retrievableThrough": "2026-08-23",
    "kinds": {},
}


class TestSpan:
    """A park and a ride describe their coverage with different field names.

    Without span() every caller writes that branch before they can ask their
    first question, and the two ends they want are the two they are most
    likely to confuse: what the archive holds, versus what their key may
    actually retrieve.
    """

    def _span_for(self, payload):
        tp = ThemeParks(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)),
            cache=False,
        )
        return tp.entity("any").history.span()

    def test_park_coverage_reads_through_summary(self):
        span = self._span_for(PARK_COVERAGE)
        assert span.archive_from == _d(2021, 7, 3)
        assert span.recorded_to == _d(2026, 9, 22)
        assert span.retrievable_through == _d(2026, 8, 23)

    def test_entity_coverage_reads_the_top_level_fields(self):
        span = self._span_for(ENTITY_COVERAGE)
        assert span.archive_from == _d(2021, 7, 3)
        assert span.recorded_to == _d(2026, 9, 22)
        assert span.retrievable_through == _d(2026, 8, 23)

    def test_both_shapes_produce_the_same_span(self):
        # The whole point: one shape out, whatever went in.
        assert self._span_for(PARK_COVERAGE) == self._span_for(ENTITY_COVERAGE)

    def test_retrievable_through_is_not_the_same_as_recorded_to(self):
        # A backfill bounded by recorded_to walks past the entitlement and
        # into 403s. The fixture keeps them a month apart so a helper that
        # returned the wrong one could not pass.
        span = self._span_for(PARK_COVERAGE)
        assert span.retrievable_through < span.recorded_to

    def test_span_feeds_days_directly(self):
        calls = []

        def handler(req):
            calls.append(req.url)
            if "coverage" in req.url.path:
                return httpx.Response(200, json=PARK_COVERAGE)
            return httpx.Response(200, json=daily_entity([day("2021-07-03")]))

        tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False)
        history = tp.entity("park-1").history
        span = history.span()
        rows = list(history.days(span.archive_from, span.retrievable_through))
        assert [r.date for _, r in rows] == [_d(2021, 7, 3)]
        # `from` is a Python keyword, so the methods say start/end while the
        # wire says from/to. This pins that translation.
        assert "from=2021-07-03" in str(calls[-1])
        assert "to=2026-08-23" in str(calls[-1])


class TestBudgetErrorIsReachableWithDefaults:
    """The one that matters: the shipped retry config, not a test-only one.

    Every other budget test in this file builds a client with retries turned
    off, which is exactly how the original defect hid. With the defaults the
    transport slept through the server's Retry-After three times before the
    history layer ever saw the 429, so a spent budget cost about two and a
    half hours of silence and BudgetExhaustedError was unreachable in
    practice. Only `sleep` is substituted here - the retry policy under test
    is the real one.
    """

    def _client(self, retry_after, slept):
        def handler(request):
            return httpx.Response(429, headers={"retry-after": retry_after}, json={})

        tp = ThemeParks(transport=httpx.MockTransport(handler), cache=False)
        tp.raw._t._sleep = slept.append
        return tp

    def test_a_spent_history_budget_raises_instead_of_sleeping(self):
        slept: list[float] = []
        tp = self._client("2700", slept)
        with pytest.raises(BudgetExhaustedError) as caught:
            list(tp.entity("park-1").history.days("2021-07-03", "2026-08-23"))
        assert slept == [], "the SDK slept through a 45 minute budget wait"
        assert caught.value.retry_after == 2700.0

    def test_an_ordinary_rest_429_is_still_ridden_out(self):
        # The cap must not turn every 429 into an error. A REST limit asks for
        # seconds, and retrying is the right answer.
        slept: list[float] = []
        tp = self._client("2", slept)
        with pytest.raises(RateLimitError) as caught:
            list(tp.entity("park-1").history.days("2026-09-01", "2026-09-02"))
        assert not isinstance(caught.value, BudgetExhaustedError)
        # Jittered: the gate is shared, so without a little spread every
        # waiter would wake at the same instant and re-trip the limit
        # together. One wait per retry, taken once, never doubled.
        assert len(slept) == 3
        assert all(2.0 <= s < 2.3 for s in slept), slept
