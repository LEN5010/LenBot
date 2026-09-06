"""Reflection proposes sparse knowledge revisions; it never commits or executes."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field

from len_bot.events.models import Event
from len_bot.memory.models import MemoryModel, MemoryProposal
from len_bot.memory.store import MemoryStore


class ReviewItem(MemoryModel):
    summary: str = Field(min_length=1)
    source_event_ids: list[str] = Field(min_length=1)


class ReflectionResult(MemoryModel):
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict, exclude=True)


class ReflectionEngine:
    """Scheduling and atomic commit belong to Runtime/SceneActor/EventStore."""

    def __init__(
        self,
        memory_store: MemoryStore,
        memory_gate: Any = None,
        llm_reflector: Callable[..., Awaitable[ReflectionResult]] | None = None,
        event_store: Any = None,
    ):
        self.memory_store = memory_store
        self.llm_reflector = llm_reflector

    async def reflect_on_events(
        self, scene_id: str, events: list[Event], context: dict | None = None,
    ) -> ReflectionResult:
        if not events:
            return ReflectionResult()
        if self.llm_reflector is None:
            raise RuntimeError("Reflection requires a configured work-profile agent")
        if any(event.scene_id != scene_id for event in events):
            raise ValueError("Reflection input contains another scene")
        result = await self.llm_reflector(events, context=context or {})
        if not isinstance(result, ReflectionResult):
            raise TypeError("Reflector must return ReflectionResult")
        known = {event.id for event in events}
        for proposal in result.memory_proposals:
            proposal.scope = scene_id
            if not set(proposal.evidence).issubset(known):
                raise ValueError("Reflection memory evidence must come from its original event batch")
        for item in result.review_items:
            if not set(item.source_event_ids).issubset(known):
                raise ValueError("Reflection review evidence must come from its original event batch")
        return result
