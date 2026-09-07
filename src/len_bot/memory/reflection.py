"""Reflection proposes sparse knowledge revisions; it never commits or executes."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field

from len_bot.memory.history import HistoryBatch
from len_bot.memory.models import MemoryModel, MemoryProposal
from len_bot.memory.store import MemoryStore


class ReviewItem(MemoryModel):
    summary: str = Field(min_length=1)
    source_event_ids: list[str] = Field(min_length=1)


class ReflectionResult(MemoryModel):
    summary: str = Field(min_length=1)
    key_event_ids: list[str] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict, exclude=True)


class ReflectionEngine:
    """Scheduling and atomic commit belong to Runtime/SceneActor/EventStore."""

    def __init__(
        self,
        memory_store: MemoryStore,
        llm_reflector: Callable[..., Awaitable[ReflectionResult]] | None = None,
    ):
        self.memory_store = memory_store
        self.llm_reflector = llm_reflector

    async def maintain_batch(
        self, scene_id: str, batch: HistoryBatch, context: dict | None = None,
    ) -> ReflectionResult:
        if self.llm_reflector is None:
            raise RuntimeError("History maintenance requires an explicitly configured maintenance profile")
        if batch.scene_id != scene_id:
            raise ValueError("Maintenance input contains another scene")
        result = await self.llm_reflector(batch, context=context or {})
        if not isinstance(result, ReflectionResult):
            raise TypeError("Maintenance must return ReflectionResult")
        if not set(result.key_event_ids).issubset(batch.source_event_ids):
            raise ValueError("Summary locations must come from its original batch")
        known = set(batch.complete_event_ids)
        for proposal in result.memory_proposals:
            if proposal.scope != scene_id:
                raise ValueError("Maintenance proposal scope must match its source scene")
            if not set(proposal.evidence).issubset(known):
                raise ValueError("Reflection memory evidence must come from its original event batch")
        for item in result.review_items:
            if not set(item.source_event_ids).issubset(known):
                raise ValueError("Reflection review evidence must come from its original event batch")
        return result
