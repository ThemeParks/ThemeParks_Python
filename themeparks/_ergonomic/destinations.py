"""Destinations directory helpers.

NOTE: `find` does a loose, case-insensitive, punctuation-insensitive substring
match on slug and name. Both the query and each candidate value are normalized
by lowercasing and stripping all non-alphanumeric characters before comparison,
so ``"Walt Disney World"``, ``"walt-disney-world"``, and ``"waltdisneyworld"``
all match the same destination. Returns the first destination whose normalized
slug or name contains the normalized query.

The destinations list grows over time, and entries can be renamed. If you need
a stable reference to a destination, resolve the id once using this helper,
then pin that id in your code.
"""

from __future__ import annotations

from themeparks._generated.models import DestinationEntry, DestinationsResponse
from themeparks._raw import AsyncRawClient, RawClient


def _normalize(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def _matches(destination: DestinationEntry, needle: str) -> bool:
    for attr in ("slug", "name"):
        value = getattr(destination, attr, None)
        if value and needle in _normalize(value):
            return True
    return False


class DestinationsApi:
    def __init__(self, *, raw: RawClient) -> None:
        self._raw = raw

    def list(self) -> DestinationsResponse:
        return self._raw.get_destinations()

    def find(self, query: str) -> DestinationEntry | None:
        needle = _normalize(query)
        if not needle:
            return None
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
        needle = _normalize(query)
        if not needle:
            return None
        res = await self.list()
        for d in res.destinations or []:
            if _matches(d, needle):
                return d
        return None
