"""Small, non-chat retrieval model clients with explicit protocol checks."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import httpx

from len_bot.cognition.providers import RetrievalProfile, ProviderRegistry
from len_bot.cognition.call_store import estimate_tokens


class RetrievalProtocolError(RuntimeError):
    pass


class RetrievalModels:
    def __init__(self, registry: ProviderRegistry, call_store=None, *, timeout: float = 30.0):
        self.registry, self.call_store, self.timeout = registry, call_store, timeout
        self._http_clients: dict[str, httpx.AsyncClient] = {}

    async def close(self):
        await asyncio.gather(*(client.aclose() for client in self._http_clients.values()))
        self._http_clients.clear()

    async def _account(self, *, scene_id: str, purpose: str, profile: RetrievalProfile, text: str):
        if self.call_store is None:
            return None
        return await self.call_store.begin_model_call(
            scene_id=scene_id, episode_id=None, job_id=None, batch_id=None,
            role="retrieval", purpose=purpose, provider_id=profile.provider_id,
            model=profile.model, reasoning_effort=None,
            estimate={"method": "text-estimate-v1", "input_tokens": estimate_tokens(text), "parts": {"text": estimate_tokens(text)}})

    async def embed(self, profile: RetrievalProfile, texts: list[str], *, scene_id: str = "", purpose: str = "embedding_query") -> list[list[float]]:
        if not texts or any(not isinstance(item, str) or not item.strip() for item in texts):
            raise ValueError("Embedding input must contain non-empty text")
        binding = self.registry.resolve_retrieval(profile, purpose="embedding")
        call_id = await self._account(scene_id=scene_id, purpose=purpose, profile=profile, text="\n".join(texts))
        started, usage = time.monotonic(), None
        try:
            raw = await binding.client.embeddings.with_raw_response.create(model=binding.model, input=texts)
            body = raw.http_response.json()
            usage = body.get("usage") if isinstance(body, dict) else None
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise RetrievalProtocolError("embedding response data length does not match input")
            vectors = [None] * len(texts)
            dimension = None
            seen_indexes = set()
            for item in data:
                index = item.get("index") if isinstance(item, dict) else None
                if type(index) is not int or index < 0 or index >= len(texts) or index in seen_indexes:
                    raise RetrievalProtocolError("embedding response contains invalid or duplicate indexes")
                seen_indexes.add(index)
                vector = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(vector, list) or not vector or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
                    raise RetrievalProtocolError("embedding response contains an invalid vector")
                if dimension is None: dimension = len(vector)
                if len(vector) != dimension: raise RetrievalProtocolError("embedding dimensions differ")
                vectors[index] = [float(x) for x in vector]
            if len(seen_indexes) != len(texts) or any(vector is None for vector in vectors):
                raise RetrievalProtocolError("embedding response indexes do not cover input")
            if profile.dimension is not None and dimension != profile.dimension:
                raise RetrievalProtocolError(f"embedding dimension {dimension} != configured {profile.dimension}")
            if call_id is not None:
                await self.call_store.end_model_call(call_id, status="completed", usage=usage)
            return vectors
        except BaseException as error:
            if call_id is not None:
                await asyncio.shield(self.call_store.end_model_call(call_id, status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed", usage=usage, error_type=type(error).__name__))
            raise

    async def rerank(self, profile: RetrievalProfile, query: str, documents: list[str], *, scene_id: str = "") -> list[int]:
        if not query.strip() or not documents:
            return []
        if profile.protocol != "cohere_v1":
            raise RetrievalProtocolError("rerank protocol is not explicitly configured")
        provider = self.registry._providers.get(profile.provider_id)
        if provider is None or not provider.enabled:
            raise LookupError(f"Provider {profile.provider_id!r} is missing or disabled")
        if not provider.api_key.strip():
            raise LookupError(f"Provider {profile.provider_id!r} has no configured API key")
        if provider.models and profile.model not in provider.models:
            raise LookupError(f"Bound model {profile.model!r} is unavailable on {provider.id!r}")
        call_id = await self._account(scene_id=scene_id, purpose="rerank", profile=profile, text=query + "\n" + "\n".join(documents))
        started, usage = time.monotonic(), None
        try:
            url = provider.base_url.rstrip("/") + "/rerank"
            client = self._http_clients.get(provider.id)
            if client is None:
                client = httpx.AsyncClient(timeout=provider.timeout_seconds, trust_env=False)
                self._http_clients[provider.id] = client
            response = await client.post(url, headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
                                                    json={"model": profile.model, "query": query, "documents": documents})
            response.raise_for_status(); body = response.json(); usage = body.get("usage") if isinstance(body, dict) else None
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list): raise RetrievalProtocolError("rerank response has no results array")
            indexes = [item.get("index") for item in results if isinstance(item, dict)]
            if len(indexes) != len(documents) or set(indexes) != set(range(len(documents))) or any(type(i) is not int or i < 0 or i >= len(documents) for i in indexes):
                raise RetrievalProtocolError("rerank response contains an invalid candidate index")
            if call_id is not None: await self.call_store.end_model_call(call_id, status="completed", usage=usage)
            return indexes
        except BaseException as error:
            if call_id is not None: await asyncio.shield(self.call_store.end_model_call(call_id, status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed", usage=usage, error_type=type(error).__name__))
            raise
