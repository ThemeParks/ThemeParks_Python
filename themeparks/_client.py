"""Sync and async top-level client classes."""

from __future__ import annotations

from importlib import metadata
from typing import Any, Callable

import httpx

from themeparks._cache import Cache, CacheConfig, InMemoryLRUCache, ttl_for_path
from themeparks._ergonomic.destinations import AsyncDestinationsApi, DestinationsApi
from themeparks._ergonomic.entity import AsyncEntityHandle, EntityHandle
from themeparks._ratelimit import RateLimits
from themeparks._raw import AsyncRawClient, RawClient
from themeparks._transport import AsyncTransport, RetryConfig, SyncTransport

DEFAULT_BASE_URL = "https://api.themeparks.wiki/v1"


def _package_version() -> str:
    """Read the installed version rather than restating it.

    This was a literal, and it said 2.0.0 in a package at 3.1.0: every request
    the SDK made announced a version two majors old, and nothing failed. A
    literal only stays right while someone remembers to change it, and nobody
    did across two releases.
    """
    try:
        return metadata.version("themeparks")
    except metadata.PackageNotFoundError:  # running from a source tree
        return "0+unknown"


PACKAGE_VERSION = _package_version()


def _default_user_agent() -> str:
    return f"themeparks-sdk-py/{PACKAGE_VERSION}"


def _build_cache(opt: Cache | bool | CacheConfig | None) -> Cache | None:
    if opt is False:
        return None
    if opt is True or opt is None:
        return InMemoryLRUCache(max_entries=CacheConfig().max_entries)
    if isinstance(opt, CacheConfig):
        return InMemoryLRUCache(max_entries=opt.max_entries)
    return opt  # user-supplied adapter


class _CachingSyncTransport:
    def __init__(self, inner: SyncTransport, cache: Cache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def rate_limit(self) -> RateLimits:
        """Whatever the inner transport last learned.

        A cache HIT sends no request and so learns nothing, which is correct:
        the figures then keep saying what the last real response said. They
        are not invalidated by a hit, because a hit spent no budget either.
        """
        return self._inner.rate_limit

    def get(self, path: str) -> Any:
        ttl = ttl_for_path(path)
        if ttl > 0:
            hit = self._cache.get(path)
            if hit is not None:
                return hit
        value = self._inner.get(path)
        if ttl > 0:
            self._cache.set(path, value, ttl)
        return value


class _CachingAsyncTransport:
    def __init__(self, inner: AsyncTransport, cache: Cache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def rate_limit(self) -> RateLimits:
        """Whatever the inner transport last learned.

        A cache HIT sends no request and so learns nothing, which is correct:
        the figures then keep saying what the last real response said. They
        are not invalidated by a hit, because a hit spent no budget either.
        """
        return self._inner.rate_limit

    async def get(self, path: str) -> Any:
        ttl = ttl_for_path(path)
        if ttl > 0:
            hit = self._cache.get(path)
            if hit is not None:
                return hit
        value = await self._inner.get(path)
        if ttl > 0:
            self._cache.set(path, value, ttl)
        return value


class ThemeParks:
    """Synchronous top-level client for the ThemeParks.wiki API.

    Wraps an ``httpx.Client`` with retries and an in-memory LRU cache that is
    on by default (per-endpoint TTLs live in :mod:`themeparks._cache`). Exposes
    both a raw surface (``tp.raw``) of 1:1 OpenAPI operations and an ergonomic
    surface (``tp.destinations``, ``tp.entity(id)``) that returns pydantic
    models. Use as a context manager so the underlying HTTP client is closed.

    Example:
        >>> from themeparks import ThemeParks
        >>> with ThemeParks() as tp:
        ...     dests = tp.destinations.list()

    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str | None = None,
        timeout: float = 10.0,
        retry: RetryConfig | None = None,
        cache: Cache | bool | CacheConfig | None = None,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        sync_t = SyncTransport(
            client=self._client,
            base_url=base_url,
            user_agent=user_agent or _default_user_agent(),
            retry=retry or RetryConfig(),
            api_key=api_key,
        )
        cache_impl = _build_cache(cache)
        inner: Any = _CachingSyncTransport(sync_t, cache_impl) if cache_impl else sync_t
        self.raw = RawClient(transport=inner)

        self._entity_ctor: Callable[[str], EntityHandle] = lambda eid: EntityHandle(
            raw=self.raw, entity_id=eid
        )
        self.destinations = DestinationsApi(raw=self.raw)

    @property
    def rate_limit(self) -> RateLimits:
        """What the server last said about your two budgets.

        `rate_limit.rest` is the per-minute REST meter; `rate_limit.history`
        is the separate hourly history budget. Every field can be None,
        because every field can be legitimately absent: an unmetered plan
        advertises nothing, and neither does a publicly cacheable response,
        since the figures belong to whoever populated the cache.

        None therefore means "the server did not say", never "nothing left".

            with ThemeParks(api_key=KEY) as tp:
                tp.entity(park).live()
                print(tp.rate_limit.rest.remaining)   # e.g. 299
        """
        return self.raw._t.rate_limit

    def entity(self, entity_id: str) -> EntityHandle:
        return self._entity_ctor(entity_id)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ThemeParks:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"ThemeParks(base_url={str(self._client.base_url).rstrip('/')!r})"


class AsyncThemeParks:
    """Asynchronous mirror of :class:`ThemeParks`.

    Backed by ``httpx.AsyncClient`` with the same retry, caching, and dual raw
    + ergonomic surfaces as the sync client. Enter with ``async with`` so the
    underlying client is closed cleanly. All ergonomic methods that hit the
    network are coroutines.

    Example:
        >>> from themeparks import AsyncThemeParks
        >>> async with AsyncThemeParks() as tp:
        ...     dests = await tp.destinations.list()

    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str | None = None,
        timeout: float = 10.0,
        retry: RetryConfig | None = None,
        cache: Cache | bool | CacheConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        async_t = AsyncTransport(
            client=self._client,
            base_url=base_url,
            user_agent=user_agent or _default_user_agent(),
            retry=retry or RetryConfig(),
            api_key=api_key,
        )
        cache_impl = _build_cache(cache)
        inner: Any = _CachingAsyncTransport(async_t, cache_impl) if cache_impl else async_t
        self.raw = AsyncRawClient(transport=inner)

        self._entity_ctor: Callable[[str], AsyncEntityHandle] = lambda eid: AsyncEntityHandle(
            raw=self.raw, entity_id=eid
        )
        self.destinations = AsyncDestinationsApi(raw=self.raw)

    @property
    def rate_limit(self) -> RateLimits:
        """What the server last said about your two budgets.

        `rate_limit.rest` is the per-minute REST meter; `rate_limit.history`
        is the separate hourly history budget. Every field can be None,
        because every field can be legitimately absent: an unmetered plan
        advertises nothing, and neither does a publicly cacheable response,
        since the figures belong to whoever populated the cache.

        None therefore means "the server did not say", never "nothing left".

            with ThemeParks(api_key=KEY) as tp:
                tp.entity(park).live()
                print(tp.rate_limit.rest.remaining)   # e.g. 299
        """
        return self.raw._t.rate_limit

    def entity(self, entity_id: str) -> AsyncEntityHandle:
        return self._entity_ctor(entity_id)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncThemeParks:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def __repr__(self) -> str:
        return f"AsyncThemeParks(base_url={str(self._client.base_url).rstrip('/')!r})"
