"""Destinations directory helpers.

NOTE: `find` does a loose, case-insensitive match on slug and name. The
destinations list grows over time, and entries can be renamed. If you need
a stable reference to a destination, resolve the id once using this helper,
then pin that id in your code.
"""

from __future__ import annotations

from themeparks._generated.models import DestinationEntry, DestinationsResponse
from themeparks._raw import AsyncRawClient, RawClient


def _matches(destination: DestinationEntry, needle: str) -> bool:
    slug = destination.slug
    name = destination.name
    return bool((slug and slug.lower() == needle) or (name and name.lower() == needle))


class DestinationsApi:
    def __init__(self, *, raw: RawClient) -> None:
        self._raw = raw

    def list(self) -> DestinationsResponse:
        return self._raw.get_destinations()

    def find(self, query: str) -> DestinationEntry | None:
        needle = query.strip().lower()
        for d in self.list().destinations or []:
            if _matches(d, needle):
                return d
        return None


class AsyncDestinationsApi:
    def __init__(self, *, raw: AsyncRawClient) -> None:
        self._raw = raw

    async def list(self) -> DestinationsResponse:
        return await self._raw.get_destinations()

    async def find(self, query: str) -> DestinationEntry | None:
        needle = query.strip().lower()
        res = await self.list()
        for d in res.destinations or []:
            if _matches(d, needle):
                return d
        return None
