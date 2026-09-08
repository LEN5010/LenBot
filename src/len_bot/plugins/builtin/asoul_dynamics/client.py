"""One HTTPX client for the site's existing /search, /on-this-day and /fanart API.

The endpoint contract comes from LEN5010/astrbot_plugin_dynamic_asoul@7ccd900.
No unobserved detail endpoint, second HTTP library or source fallback is used.
"""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from .config import DynamicsConfig
from .models import DynamicPage, FanartPage, HistoricalDayPage, RandomFanartPage

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings


@dataclass(frozen=True)
class SourceSnapshot:
    data: dict
    fetched_at: float
    expires_at: float
    url: str


class DynamicsClient:
    def __init__(self, config: DynamicsConfig, members: list[MemberSettings]):
        self.config = config
        self.members = tuple(members)
        self._client = httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=False,
            follow_redirects=False, headers={"User-Agent": config.user_agent})
        self._cache: dict[tuple, SourceSnapshot] = {}
        self._records: dict[str, SourceSnapshot] = {}
        self._lock = asyncio.Lock()
        self.last_success_at = None
        self.last_error_at = None
        self.last_error = None

    async def close(self):
        await self._client.aclose()

    def member_id(self, query: str | None) -> str | None:
        if query is None:
            return None
        key = query.casefold()
        matches = [member for member in self.members
                   if key in {member.name.casefold(), *(alias.casefold() for alias in member.aliases)}]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous configured dynamics member; use null explicitly for all members")
        return "uid:" + str(matches[0].bilibili_uid)

    async def _request(self, endpoint: str, params: dict[str, Any], response_type, *, ttl: float,
                       cache: bool = True) -> tuple[SourceSnapshot, bool]:
        request_params = {key: value for key, value in params.items() if value is not None}
        key = (endpoint, tuple(sorted(request_params.items())))
        async with self._lock:
            snapshot = self._cache.get(key)
            if cache and snapshot and time.time() < snapshot.expires_at:
                return snapshot, True
            try:
                response = await self._client.get(self.config.api_base_url + endpoint, params=request_params)
                response.raise_for_status()
                data = response_type.model_validate(response.json()).model_dump(mode="json")
            except Exception as error:
                self.last_error_at = time.time()
                self.last_error = f"{type(error).__name__}: {error}"
                raise
            fetched_at = time.time()
            snapshot = SourceSnapshot(data, fetched_at, fetched_at + ttl, str(response.url))
            if cache:
                self._cache[key] = snapshot
            self.last_success_at = fetched_at
            for item in data["items"]:
                if "dynamicId" in item:
                    self._records[item["dynamicId"]] = SourceSnapshot(copy.deepcopy(item), fetched_at,
                        fetched_at + ttl, str(response.url))
            return snapshot, False

    async def search(self, *, query: str | None, member: str | None, cursor: str | None,
                     dynamic_type: str | None, sort: str, limit: int):
        return await self._request("/search", {"q": query, "member": self.member_id(member),
            "cursor": cursor, "type": dynamic_type, "sort": sort, "limit": limit},
            DynamicPage, ttl=self.config.cache_ttl_seconds)

    async def on_this_day(self, month_day: str, limit: int):
        return await self._request("/on-this-day", {"monthDay": month_day,
            "sort": self.config.on_this_day_sort, "limit": limit}, HistoricalDayPage,
            ttl=self.config.on_this_day_cache_ttl_seconds)

    async def fanart(self, *, query: str | None, character: str | None, content_type: str | None,
                     category: str | None, kind: str | None, cursor: str | None, random: bool):
        params = {"q": query, "character": character, "contentType": content_type, "category": category,
                  "kind": kind, "cursor": cursor, "limit": 1 if random else self.config.default_limit}
        if random:
            params["random"] = 1
        else:
            params["sort"] = self.config.fanart_sort
        return await self._request("/fanart", params, RandomFanartPage if random else FanartPage,
            ttl=self.config.cache_ttl_seconds, cache=not random)

    def read_obtained_dynamic(self, dynamic_id: str) -> SourceSnapshot:
        snapshot = self._records.get(dynamic_id)
        if snapshot is None:
            raise ValueError("This source record has not been retrieved; query latest dynamics or search first")
        if time.time() >= snapshot.expires_at:
            raise ValueError("This source record is stale; refresh the original list query before reading it")
        # This is the same source-provided record, not a fabricated full-detail API.
        return snapshot

    def source_status(self) -> dict:
        return {"source_url": self.config.api_base_url, "last_success_at": self.last_success_at,
                "last_error_at": self.last_error_at, "last_error": self.last_error,
                "data_scope": "该源已抓取的动态和二创；不代表全平台绝对最新"}
