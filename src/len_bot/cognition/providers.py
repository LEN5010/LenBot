"""Provider Registry (ADR-0020): multi-provider LLM configuration and tier routing.

The registry is the single authority for "which client + which model serves this
cognitive tier". Providers are OpenAI-compatible endpoints; client instances are
created lazily and cached per provider until its connection settings change.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from len_bot.cognition.router import CognitiveTier

logger = logging.getLogger(__name__)


class ProviderConfig(BaseModel):
    id: str
    api_style: str = Field(default="openai", description="Only openai-compatible is supported in V2")
    base_url: str
    api_key: str = Field(default="", description="Write-only; never echoed back by the API")
    enabled: bool = True
    timeout_seconds: float = 60.0


class RouteTarget(BaseModel):
    provider_id: str
    model: str


class RoutingConfig(BaseModel):
    normal: RouteTarget
    deliberate: RouteTarget


@dataclass
class RouteResolution:
    provider_id: str
    model: str
    client: AsyncOpenAI


def _connection_fingerprint(p: ProviderConfig) -> tuple:
    return (p.api_style, p.base_url, p.api_key, p.timeout_seconds)


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderConfig] = {}
        self._routing: Optional[RoutingConfig] = None
        self._clients: dict[str, AsyncOpenAI] = {}
        self._fingerprints: dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def apply_update(self, providers: list[ProviderConfig], routing: RoutingConfig) -> None:
        """Hot-swap providers + routing (ADR-0020). Takes effect on the next resolve()."""
        provider_list = list(providers)
        seen = set()
        for p in provider_list:
            if p.id in seen:
                raise ValueError(f"Duplicate provider id: {p.id}")
            seen.add(p.id)
        for target in (routing.normal, routing.deliberate):
            if target.provider_id not in seen:
                raise ValueError(f"Route references unknown provider: {target.provider_id}")

        async with self._lock:
            new_providers = {p.id: p for p in provider_list}
            # Drop cached clients whose connection settings changed or disappeared
            for pid in list(self._clients.keys()):
                cfg = new_providers.get(pid)
                if cfg is None or _connection_fingerprint(cfg) != self._fingerprints.get(pid):
                    self._clients.pop(pid, None)
            self._providers = new_providers
            self._routing = routing
        logger.info("ProviderRegistry updated: %d provider(s), normal=%s/%s deliberate=%s/%s",
                    len(provider_list), routing.normal.provider_id, routing.normal.model,
                    routing.deliberate.provider_id, routing.deliberate.model)

    def resolve(self, tier: CognitiveTier) -> RouteResolution:
        if self._routing is None:
            raise LookupError("No provider routing configured")
        target = self._routing.deliberate if tier == CognitiveTier.DELIBERATE else self._routing.normal
        provider = self._providers.get(target.provider_id)
        if provider is None or not provider.enabled:
            raise LookupError(f"Provider '{target.provider_id}' is missing or disabled")

        client = self._clients.get(provider.id)
        if client is None:
            client = AsyncOpenAI(
                api_key=provider.api_key or "missing",
                base_url=provider.base_url,
                timeout=provider.timeout_seconds,
            )
            self._clients[provider.id] = client
            self._fingerprints[provider.id] = _connection_fingerprint(provider)
        return RouteResolution(provider_id=provider.id, model=target.model, client=client)

    def export(self) -> dict:
        """Full state for persistence (including api_key — DB is the secret store)."""
        return {
            "providers": [p.model_dump() for p in self._providers.values()],
            "routing": self._routing.model_dump() if self._routing else None,
        }

    def snapshot(self) -> dict:
        """API-safe view: providers with masked key hints + routing."""
        providers = []
        for p in self._providers.values():
            data = p.model_dump()
            key = data.pop("api_key", "")
            data["api_key_masked"] = (key[:3] + "..." + key[-4:]) if len(key) >= 8 else ("已设置" if key else "")
            providers.append(data)
        return {
            "providers": providers,
            "routing": self._routing.model_dump() if self._routing else None,
        }
