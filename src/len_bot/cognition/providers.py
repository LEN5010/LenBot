"""Configured model profiles, resolved once for each cognitive run."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)
ModelRole = Literal["conversation", "work", "maintenance"]


class ProviderConfig(BaseModel):
    id: str
    api_style: str = Field(default="openai", description="OpenAI-compatible chat completions")
    base_url: str
    api_key: str = Field(default="", description="Write-only; never echoed back by the API")
    enabled: bool = True
    timeout_seconds: float = 60.0
    models: list[str] = Field(default_factory=list)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    model: str
    reasoning_effort: str | None = None

    @field_validator("provider_id", "model")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model profile provider and model must be nonempty")
        return value.strip()

    @field_validator("reasoning_effort")
    @classmethod
    def normalize_effort(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation: ModelProfile
    work: ModelProfile
    maintenance: ModelProfile | None = None


@dataclass(frozen=True)
class RouteResolution:
    provider_id: str
    model: str
    client: AsyncOpenAI
    reasoning_effort: str | None = None
    role: ModelRole = "conversation"


def _connection_fingerprint(provider: ProviderConfig) -> tuple:
    return (provider.api_style, provider.base_url, provider.api_key, provider.timeout_seconds)


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderConfig] = {}
        self._routing: RoutingConfig | None = None
        self._clients: dict[str, AsyncOpenAI] = {}
        self._fingerprints: dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def apply_update(self, providers: list[ProviderConfig], routing: RoutingConfig | None) -> None:
        """Publish profiles for subsequent runs; existing resolutions stay frozen."""
        provider_list = [provider.model_copy(deep=True) for provider in providers]
        seen: set[str] = set()
        for provider in provider_list:
            if provider.id in seen:
                raise ValueError(f"Duplicate provider id: {provider.id}")
            if provider.api_style != "openai":
                raise ValueError(f"Unsupported provider API style: {provider.api_style}")
            seen.add(provider.id)
        for target in (routing.conversation, routing.work, routing.maintenance) if routing is not None else ():
            if target is not None and target.provider_id not in seen:
                raise ValueError(f"Route references unknown provider: {target.provider_id}")
        async with self._lock:
            new_providers = {provider.id: provider for provider in provider_list}
            for provider_id in list(self._clients):
                provider = new_providers.get(provider_id)
                if provider is None or _connection_fingerprint(provider) != self._fingerprints.get(provider_id):
                    # A running gateway owns its client until that run ends.
                    self._clients.pop(provider_id)
                    self._fingerprints.pop(provider_id, None)
            self._providers = new_providers
            self._routing = routing.model_copy(deep=True) if routing is not None else None
        if routing is None:
            logger.info("Providers updated: %d provider(s), model profiles not configured", len(provider_list))
        else:
            logger.info("Model profiles updated: conversation=%s/%s work=%s/%s",
                        routing.conversation.provider_id, routing.conversation.model,
                        routing.work.provider_id, routing.work.model)

    def _client_for(self, provider_id: str) -> AsyncOpenAI:
        provider = self._providers.get(provider_id)
        if provider is None or not provider.enabled:
            raise LookupError(f"Provider '{provider_id}' is missing or disabled")
        client = self._clients.get(provider.id)
        if client is None:
            client = AsyncOpenAI(api_key=provider.api_key or "missing", base_url=provider.base_url,
                                 timeout=provider.timeout_seconds, max_retries=0)
            self._clients[provider.id] = client
            self._fingerprints[provider.id] = _connection_fingerprint(provider)
        return client

    def resolve(self, role: ModelRole = "conversation") -> RouteResolution:
        if self._routing is None:
            raise LookupError("No model profiles configured")
        if role not in ("conversation", "work", "maintenance"):
            raise ValueError(f"Unknown model role: {role}")
        profile = getattr(self._routing, role)
        if profile is None:
            raise LookupError(f"Model profile {role!r} is not configured")
        return self.resolve_profile(profile, role=role)

    def resolve_profile(self, profile: ModelProfile, role: ModelRole = "work") -> RouteResolution:
        """Resume a frozen profile using current credentials, never a default model."""
        provider = self._providers.get(profile.provider_id)
        if provider is None or not provider.enabled:
            raise LookupError(f"Provider {profile.provider_id!r} is missing or disabled")
        if provider.models and profile.model not in provider.models:
            raise LookupError(f"Bound model {profile.model!r} is unavailable on {provider.id!r}")
        return RouteResolution(provider_id=profile.provider_id, model=profile.model,
                               reasoning_effort=profile.reasoning_effort, role=role,
                               client=self._client_for(profile.provider_id))

    async def list_models(self, provider_id: str) -> list[str]:
        response = await self._client_for(provider_id).models.list()
        return sorted({str(item.id) for item in response.data if getattr(item, "id", None)})

    def has_live_provider(self) -> bool:
        if self._routing is None:
            return False
        provider = self._providers.get(self._routing.conversation.provider_id)
        if provider is None or not provider.enabled:
            return False
        key = provider.api_key.strip()
        return bool(key) and key != "missing"

    def export(self) -> dict:
        return {"providers": [provider.model_dump() for provider in self._providers.values()],
                "routing": self._routing.model_dump() if self._routing else None}

    def snapshot(self) -> dict:
        providers = []
        for provider in self._providers.values():
            data = provider.model_dump()
            key = data.pop("api_key", "")
            data["api_key_masked"] = (key[:3] + "..." + key[-4:]) if len(key) >= 8 else ("已设置" if key else "")
            providers.append(data)
        return {"providers": providers, "routing": self._routing.model_dump() if self._routing else None}
