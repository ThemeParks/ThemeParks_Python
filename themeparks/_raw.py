"""Low-level 1:1 wrappers over the ThemeParks OpenAPI operations."""

from __future__ import annotations

from typing import Any, Union
from urllib.parse import quote, urlencode

from themeparks._generated.models import (
    DestinationsResponse,
    EntityChildrenResponse,
    EntityData,
    EntityLiveDataResponse,
    EntityScheduleResponse,
    HistoryCoverageDocument,
    HistoryDailyEnvelope,
    HistoryEnvelope,
    HistoryParkCoverageDocument,
    HistoryParkDailyEnvelope,
    HistoryParkRawEnvelope,
)
from themeparks._transport import AsyncTransport, SyncTransport

AnyTransport = Union[SyncTransport, AsyncTransport]


def _path_entity(entity_id: str) -> str:
    return f"/entity/{quote(entity_id, safe='')}"


def _history_query(
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> str:
    """Build the query string for a history request.

    ``date`` cannot be combined with ``start``/``end``: the API answers 400
    INVALID_DATE for that pair, so the SDK does not let a caller construct it.
    """
    if date is not None and (start is not None or end is not None):
        raise ValueError("pass either date, or from/to, not both")
    params = [(k, v) for k, v in (("date", date), ("from", start), ("to", end)) if v is not None]
    return f"?{urlencode(params)}" if params else ""


# The same path answers a single entity and a whole PARK with two different
# shapes, and the response tells you which by carrying `entities`. Sniffing the
# payload keeps the raw layer 1:1 with the operation rather than making the
# caller pick a method based on something they may not know yet.
def _parse_raw_history(payload: Any) -> HistoryEnvelope | HistoryParkRawEnvelope:
    if isinstance(payload, dict) and "entities" in payload:
        return HistoryParkRawEnvelope.model_validate(payload)
    return HistoryEnvelope.model_validate(payload)


def _parse_daily_history(payload: Any) -> HistoryDailyEnvelope | HistoryParkDailyEnvelope:
    if isinstance(payload, dict) and "entities" in payload:
        return HistoryParkDailyEnvelope.model_validate(payload)
    return HistoryDailyEnvelope.model_validate(payload)


def _parse_coverage(payload: Any) -> HistoryCoverageDocument | HistoryParkCoverageDocument:
    if isinstance(payload, dict) and "entities" in payload:
        return HistoryParkCoverageDocument.model_validate(payload)
    return HistoryCoverageDocument.model_validate(payload)


class RawClient:
    """Thin, 1:1 synchronous wrapper over the ThemeParks OpenAPI operations.

    Each method maps to a single HTTP GET and returns a validated pydantic
    model from :mod:`themeparks._generated.models`. No convenience logic
    lives here: this is the layer that ergonomic helpers build on, and the
    same layer users can reach directly via ``tp.raw`` when they need the
    untransformed response shape.
    """

    def __init__(self, *, transport: SyncTransport) -> None:
        self._t = transport

    def get_destinations(self) -> DestinationsResponse:
        return DestinationsResponse.model_validate(self._t.get("/destinations"))

    def get_entity(self, entity_id: str) -> EntityData:
        return EntityData.model_validate(self._t.get(_path_entity(entity_id)))

    def get_entity_children(self, entity_id: str) -> EntityChildrenResponse:
        return EntityChildrenResponse.model_validate(
            self._t.get(_path_entity(entity_id) + "/children")
        )

    def get_entity_live(self, entity_id: str) -> EntityLiveDataResponse:
        return EntityLiveDataResponse.model_validate(self._t.get(_path_entity(entity_id) + "/live"))

    def get_entity_schedule(self, entity_id: str) -> EntityScheduleResponse:
        return EntityScheduleResponse.model_validate(
            self._t.get(_path_entity(entity_id) + "/schedule")
        )

    def get_entity_schedule_month(
        self, entity_id: str, year: int, month: int
    ) -> EntityScheduleResponse:
        return EntityScheduleResponse.model_validate(
            self._t.get(_path_entity(entity_id) + f"/schedule/{year}/{month:02d}")
        )

    def get_entity_history(
        self,
        entity_id: str,
        *,
        date: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> HistoryEnvelope | HistoryParkRawEnvelope:
        """Every recorded change for an entity, or for a whole park."""
        query = _history_query(date, start, end)
        return _parse_raw_history(self._t.get(_path_entity(entity_id) + "/history" + query))

    def get_entity_history_daily(
        self,
        entity_id: str,
        *,
        date: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> HistoryDailyEnvelope | HistoryParkDailyEnvelope:
        """One summary row per park-local day, for an entity or a whole park."""
        query = _history_query(date, start, end)
        return _parse_daily_history(self._t.get(_path_entity(entity_id) + "/history/daily" + query))

    def get_entity_history_coverage(
        self, entity_id: str
    ) -> HistoryCoverageDocument | HistoryParkCoverageDocument:
        """What history exists for an entity, field by field."""
        return _parse_coverage(self._t.get(_path_entity(entity_id) + "/history/coverage"))

    def get_path(self, path: str) -> Any:
        """Fetch a path the API itself handed us.

        Only used to follow a `next` page link. The history endpoints page by
        returning an absolute URL, and re-deriving it from its parts is how a
        client drifts from the server's own idea of where the next page is.
        """
        return self._t.get(path)


class AsyncRawClient:
    """Asynchronous mirror of :class:`RawClient`.

    Same 1:1 mapping onto the OpenAPI operations, but every method is a
    coroutine. Returned pydantic models are identical to the sync client.
    Reachable as ``tp.raw`` on :class:`~themeparks.AsyncThemeParks`.
    """

    def __init__(self, *, transport: AsyncTransport) -> None:
        self._t = transport

    async def get_destinations(self) -> DestinationsResponse:
        return DestinationsResponse.model_validate(await self._t.get("/destinations"))

    async def get_entity(self, entity_id: str) -> EntityData:
        return EntityData.model_validate(await self._t.get(_path_entity(entity_id)))

    async def get_entity_children(self, entity_id: str) -> EntityChildrenResponse:
        return EntityChildrenResponse.model_validate(
            await self._t.get(_path_entity(entity_id) + "/children")
        )

    async def get_entity_live(self, entity_id: str) -> EntityLiveDataResponse:
        return EntityLiveDataResponse.model_validate(
            await self._t.get(_path_entity(entity_id) + "/live")
        )

    async def get_entity_schedule(self, entity_id: str) -> EntityScheduleResponse:
        return EntityScheduleResponse.model_validate(
            await self._t.get(_path_entity(entity_id) + "/schedule")
        )

    async def get_entity_schedule_month(
        self, entity_id: str, year: int, month: int
    ) -> EntityScheduleResponse:
        return EntityScheduleResponse.model_validate(
            await self._t.get(_path_entity(entity_id) + f"/schedule/{year}/{month:02d}")
        )

    async def get_entity_history(
        self,
        entity_id: str,
        *,
        date: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> HistoryEnvelope | HistoryParkRawEnvelope:
        query = _history_query(date, start, end)
        return _parse_raw_history(await self._t.get(_path_entity(entity_id) + "/history" + query))

    async def get_entity_history_daily(
        self,
        entity_id: str,
        *,
        date: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> HistoryDailyEnvelope | HistoryParkDailyEnvelope:
        query = _history_query(date, start, end)
        return _parse_daily_history(
            await self._t.get(_path_entity(entity_id) + "/history/daily" + query)
        )

    async def get_entity_history_coverage(
        self, entity_id: str
    ) -> HistoryCoverageDocument | HistoryParkCoverageDocument:
        return _parse_coverage(await self._t.get(_path_entity(entity_id) + "/history/coverage"))

    async def get_path(self, path: str) -> Any:
        return await self._t.get(path)
