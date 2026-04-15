"""Low-level 1:1 wrappers over the ThemeParks OpenAPI operations."""

from __future__ import annotations

from typing import Union
from urllib.parse import quote

from themeparks._generated.models import (
    DestinationsResponse,
    EntityChildrenResponse,
    EntityData,
    EntityLiveDataResponse,
    EntityScheduleResponse,
)
from themeparks._transport import AsyncTransport, SyncTransport

AnyTransport = Union[SyncTransport, AsyncTransport]


def _path_entity(entity_id: str) -> str:
    return f"/entity/{quote(entity_id, safe='')}"


class RawClient:
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


class AsyncRawClient:
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
