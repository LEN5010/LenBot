"""Scene-local expression vector selection and explicit index changes."""

from __future__ import annotations

import asyncio
import json
import math
import logging
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from typing import Literal

from ..config import HostConfig, LabConfig
from .store import LearningStore, StoredVector
from ..memory.embeddings import EmbeddingBatch, EmbeddingClient, EmbeddingSettings
from ..models.slots import ModelSlots
from ..models.tokens import token_record
from ..storage.store import Store, encode
from ..runtime.logs import log_event

logger = logging.getLogger(__name__)


class ExpressionIndexNeedsRebuild(ValueError):
    """Adopted expressions cannot use the configured vector index."""


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class ExpressionService:
    """One selected embedding backend per scene, without implicit index repair."""

    def __init__(self, store: Store, clients: dict[str, EmbeddingClient],
                 *, slots: ModelSlots | None = None,
                 on_update: Callable[[], None] | None = None):
        self.store = store
        self.records = LearningStore(store)
        self.clients = clients
        self.slots = slots
        self.on_update = on_update
        self.scenes = tuple(clients)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, scene: str) -> asyncio.Lock:
        return self._locks.setdefault(scene, asyncio.Lock())

    def _client(self, scene: str) -> EmbeddingClient:
        try:
            return self.clients[scene]
        except KeyError as error:
            raise ValueError(f"scene {scene!r} has no expression embedding binding") from error

    def _binding(self, scene: str) -> dict:
        settings = self._client(scene).settings
        return {"provider": settings.provider, "base_url": settings.base_url,
                "model": settings.model, "dimensions": settings.dimensions}

    def _stored(self, scene: str, vector: tuple[float, ...], dimensions: int) -> StoredVector:
        return (struct.pack(f"<{dimensions}f", *vector), encode(self._binding(scene)), dimensions)

    def validate(self, scene: str) -> int | None:
        """Require every adopted expression to match the current binding and one actual dimension."""
        return self._validate_rows(scene, self.records.adopted(scene))

    def _validate_rows(self, scene: str, adopted: list[dict]) -> int | None:
        expected = self._binding(scene)
        dimensions: int | None = None
        for item in adopted:
            data, binding, actual = (item["vector"], item["vector_binding"], item["vector_dimensions"])
            if data is None or binding is None or actual is None:
                raise ExpressionIndexNeedsRebuild(f"scene {scene} adopted expression {item['id']} lacks a vector")
            stored_binding = json.loads(binding)
            if any(stored_binding[field] != expected[field] for field in ("base_url", "model")):
                raise ExpressionIndexNeedsRebuild(f"scene {scene} expression {item['id']} uses a different embedding binding")
            if expected["dimensions"] is not None and actual != expected["dimensions"]:
                raise ExpressionIndexNeedsRebuild(f"scene {scene} expression {item['id']} differs from configured vector dimensions")
            if dimensions is None:
                dimensions = actual
            elif actual != dimensions:
                raise ExpressionIndexNeedsRebuild(f"scene {scene} has mixed expression vector dimensions")
        return dimensions

    def index_status(self, scene: str) -> dict:
        try:
            self.validate(scene)
        except ExpressionIndexNeedsRebuild as error:
            return {'needs_rebuild': True, 'reason': str(error)}
        return {'needs_rebuild': False, 'reason': None}

    async def _embed(self, scene: str, texts: list[str], *, purpose: Literal["query", "index", "reindex"],
                     turn_id: str | None = None, direct: bool = False) -> EmbeddingBatch:
        client = self._client(scene)
        async with (self.slots.slot(direct=direct, scene=scene) if self.slots is not None else nullcontext()):
            call_id = self.records.start_embedding_call(
                scene, purpose,
                {"settings": client.settings.model_dump(exclude={"api_key"}), "texts": list(texts)},
                turn_id=turn_id,
            )
            if self.on_update is not None:
                self.on_update()
            try:
                batch = await client.embed(texts)
            except BaseException as error:
                self.records.end_embedding_call(call_id, None, None, None, error=_error_text(error))
                if self.on_update is not None:
                    self.on_update()
                raise
            self.records.end_embedding_call(
                call_id, {"vector_count": len(batch.vectors), "dimensions": batch.dimensions},
                batch.usage, token_record(batch.token_usage),
            )
            if self.on_update is not None:
                self.on_update()
            return batch

    async def select(self, scene: str, query: str, *, turn_id: str, direct: bool, exclude_uids: tuple[str, ...] = ()) -> list[dict]:
        """Return up to five ranked candidate facts; the chat model decides relevance and use."""
        async with self._lock(scene):
            adopted = self.records.adopted(scene, exclude_uids=exclude_uids)
            try:
                dimensions = self._validate_rows(scene, adopted)
            except ExpressionIndexNeedsRebuild as error:
                log_event(logger, 'expression_index_unavailable', '群内说法索引需要重建',
                          level=logging.WARNING, scene=scene, error=error)
                return []
            eligible = adopted
            if not eligible:
                return []
            if not query.strip():
                raise ValueError("expression selection query must be nonblank")
            batch = await self._embed(scene, [query], purpose="query", turn_id=turn_id,
                                      direct=direct)
            if batch.dimensions != dimensions:
                raise ValueError(f"scene {scene} query embedding dimension {batch.dimensions} differs from "
                                 f"indexed expression dimension {dimensions}; stop and rebuild offline")
            needle = batch.vectors[0]
            needle_norm = math.sqrt(math.fsum(value * value for value in needle))
            ranked = []
            for item in eligible:
                vector = struct.unpack(f"<{dimensions}f", item["vector"])
                norm = math.sqrt(math.fsum(value * value for value in vector))
                similarity = math.fsum(a * b for a, b in zip(needle, vector, strict=True)) / (needle_norm * norm)
                ranked.append({"id": item["id"], "situation": item["situation"],
                               "style": item["style"], "similarity": similarity})
            ranked.sort(key=lambda item: (-item["similarity"], item["id"]))
            return ranked[:5]

    async def update(self, scene: str, id: int, *, situation: str, style: str, status: str) -> dict | None:
        async with self._lock(scene):
            old = self.records.expression(scene, id)
            if old is None:
                return None
            vector: StoredVector | None = None
            if status == "adopted" and scene in self.clients:
                dimensions = self.validate(scene)
                if old["status"] == "adopted" and old["situation"] == situation:
                    vector = self.records.vector(scene, id)
                else:
                    batch = await self._embed(scene, [situation], purpose="index")
                    if dimensions is not None and batch.dimensions != dimensions:
                        raise ValueError(f"scene {scene} new expression dimension {batch.dimensions} differs "
                                         f"from current index dimension {dimensions}; stop and rebuild offline")
                    vector = self._stored(scene, batch.vectors[0], batch.dimensions)
            item = self.records.update_expression(scene, id, situation=situation, style=style,
                                                  status=status, vector=vector)
            if self.on_update is not None:
                self.on_update()
            return item

    async def delete(self, scene: str, id: int) -> bool:
        async with self._lock(scene):
            deleted = self.records.delete_expression(scene, id)
            if deleted and self.on_update is not None:
                self.on_update()
            return deleted

    async def complete(self, scene: str, batch_id: int,
                       candidates: list[tuple[str, str, list[int]]], *, auto_adopt: bool) -> None:
        async with self._lock(scene):
            vectors: dict[tuple[str, str], StoredVector] = {}
            if auto_adopt and scene in self.clients:
                new = list(dict.fromkeys((situation, style) for situation, style, _ in candidates
                                         if self.records.find_expression(scene, situation, style) is None))
                if new:
                    dimensions = self.validate(scene)
                    batch = await self._embed(scene, [situation for situation, _ in new], purpose="index")
                    if dimensions is not None and batch.dimensions != dimensions:
                        raise ValueError(f"scene {scene} new expression dimension {batch.dimensions} differs "
                                         f"from current index dimension {dimensions}; stop and rebuild offline")
                    vectors = {key: self._stored(scene, vector, batch.dimensions)
                               for key, vector in zip(new, batch.vectors, strict=True)}
            self.records.complete(batch_id, candidates, auto_adopt=auto_adopt, vectors=vectors)
            if self.on_update is not None:
                self.on_update()

    async def rebuild(self, scene: str) -> int:
        """Explicit offline replacement; never silently repair a running index."""
        self._client(scene)
        async with self._lock(scene):
            adopted = self.records.adopted(scene)
            snapshot = [(item["id"], item["situation"]) for item in adopted]
            vectors: dict[int, StoredVector] = {}
            dimensions: int | None = None
            for start in range(0, len(adopted), 100):
                chunk = adopted[start:start + 100]
                batch = await self._embed(scene, [item["situation"] for item in chunk], purpose="reindex")
                if dimensions is None:
                    dimensions = batch.dimensions
                elif batch.dimensions != dimensions:
                    raise ValueError(f"scene {scene} reindex returned mixed dimensions; old vectors unchanged")
                vectors.update({item["id"]: self._stored(scene, vector, batch.dimensions)
                                for item, vector in zip(chunk, batch.vectors, strict=True)})
            self.records.replace_vectors(scene, snapshot, vectors)
            if self.on_update is not None:
                self.on_update()
            return len(snapshot)


@asynccontextmanager
async def open_expression_service(config: HostConfig | LabConfig, store: Store, *,
                                  slots: ModelSlots | None = None) -> AsyncIterator[ExpressionService | None]:
    """Resolve root bindings once and close each distinct embedding client once."""
    scene_settings = config.scenes if isinstance(config, HostConfig) else {config.scene: config}
    selected = {scene: settings.learning.embedding for scene, settings in scene_settings.items()
                if settings.learning is not None and settings.learning.embedding is not None}
    if not selected:
        yield None
        return
    clients: dict[str, EmbeddingClient] = {}
    shared: dict[tuple[str, str, int | None], EmbeddingClient] = {}
    async with AsyncExitStack() as stack:
        for scene, binding in selected.items():
            key = (binding.provider, binding.model, binding.dimensions)
            if key not in shared:
                provider = config.models.providers[binding.provider]
                resolved = EmbeddingSettings(**binding.model_dump(), base_url=provider.base_url,
                                             api_key=provider.api_key, proxy=provider.proxy)
                shared[key] = await stack.enter_async_context(EmbeddingClient(resolved))
            clients[scene] = shared[key]
        yield ExpressionService(store, clients, slots=slots)
