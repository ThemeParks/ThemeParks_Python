"""Sync and async top-level client classes."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from themeparks._cache import Cache, CacheConfig, InMemoryLRUCache, ttl_for_path
from themeparks._ergonomic.destinations import AsyncDestinationsApi, DestinationsApi
from themeparks._ergonomic.entity import AsyncEntityHandle, EntityHandle
from themeparks._raw import AsyncRawClient, RawClient
from themeparks._transport import AsyncTransport, RetryConfig, SyncTransport

DEFAULT_BASE_URL = "https://api.themeparks.wiki/v1"
PACKAGE_VERSION = "2.0.0a0"


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
    def __init__(  # noqa: PLR0913
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str | None = None,
        timeout: float = 10.0,
        retry: RetryConfig | None = None,
        cache: Cache | bool | CacheConfig | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport
        )
        sync_t = SyncTransport(
            client=self._client,
            base_url=base_url,
            user_agent=user_agent or _default_user_agent(),
            retry=retry or RetryConfig(),
        )
        cache_impl = _build_cache(cache)
        inner: Any = (
            _CachingSyncTransport(sync_t, cache_impl) if cache_impl else sync_t
        )
        self.raw = RawClient(transport=inner)

        self._entity_ctor: Callable[[str], EntityHandle] = lambda eid: EntityHandle(
            raw=self.raw, entity_id=eid
        )
        self.destinations = DestinationsApi(raw=self.raw)

    def entity(self, entity_id: str) -> EntityHandle:
        return self._entity_ctor(entity_id)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ThemeParks:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class AsyncThemeParks:
    def __init__(  # noqa: PLR0913
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str | None = None,
        timeout: float = 10.0,
        retry: RetryConfig | None = None,
        cache: Cache | bool | CacheConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport
        )
        async_t = AsyncTransport(
            client=self._client,
            base_url=base_url,
            user_agent=user_agent or _default_user_agent(),
            retry=retry or RetryConfig(),
        )
        cache_impl = _build_cache(cache)
        inner: Any = (
            _CachingAsyncTransport(async_t, cache_impl) if cache_impl else async_t
        )
        self.raw = AsyncRawClient(transport=inner)

        self._entity_ctor: Callable[[str], AsyncEntityHandle] = (
            lambda eid: AsyncEntityHandle(raw=self.raw, entity_id=eid)
        )
        self.destinations = AsyncDestinationsApi(raw=self.raw)

    def entity(self, entity_id: str) -> AsyncEntityHandle:
        return self._entity_ctor(entity_id)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncThemeParks:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
