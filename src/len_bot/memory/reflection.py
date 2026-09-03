import logging
import time
from typing import Optional, Callable, Awaitable, Any
from len_bot.events.models import Event
from len_bot.memory.models import EpisodeRecord, MemoryProposal
from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate

logger = logging.getLogger(__name__)

class ReflectionEngine:
    """Produces L1 Episode Records and L2 Social Memory proposals from experiences (ADR-0011, ADR-0028)."""

    def __init__(
        self,
        memory_store: MemoryStore,
        memory_gate: MemoryGate,
        llm_reflector: Optional[Callable[[list[Event]], Awaitable[tuple[EpisodeRecord, list[MemoryProposal]]]]] = None,
        event_store: Optional[Any] = None
    ):
        self.memory_store = memory_store
        self.memory_gate = memory_gate
        self.llm_reflector = llm_reflector
        self.event_store = event_store

    async def reflect_on_events(self, scene_id: str, events: list[Event]) -> tuple[Optional[EpisodeRecord], list[MemoryProposal]]:
        """Generates an L1 EpisodeRecord and L2 MemoryProposal list without persisting."""
        if not events:
            return None, []

        if self.llm_reflector:
            return await self.llm_reflector(events)
        else:
            participants = list({e.actor_id for e in events if e.actor_id})
            combined = " ".join(e.raw_text for e in events if e.raw_text)
            title = f"对话记录 ({len(events)}条)"
            tags = []
            if "直播" in combined:
                tags.append("直播")
                title = "直播话题讨论"

            episode_record = EpisodeRecord(
                scene_id=scene_id,
                title=title,
                summary=combined[:200],
                source_event_ids=[e.id for e in events],
                participants=participants,
                tags=tags,
                created_at=time.time()
            )
            return episode_record, []

    async def run_micro_reflection(self, scene_id: str, events: list[Event]) -> Optional[EpisodeRecord]:
        """Runs micro-reflection when a conversation block has completed.
        If event_store is wired, commits atomically via commit_reflection_batch (ADR-0028).
        """
        episode_record, proposals = await self.reflect_on_events(scene_id, events)
        if episode_record is None:
            return None

        max_rowid = max((int(e.metadata.get("_rowid", 0)) for e in events), default=0)
        if self.event_store:
            await self.event_store.commit_reflection_batch(
                scene_id=scene_id,
                episode_record=episode_record,
                proposals=proposals,
                new_cursor_rowid=max_rowid
            )
        else:
            await self.memory_store.save_episode(episode_record)
            for prop in proposals:
                prop.scope = scene_id
                gate_res = await self.memory_gate.commit_proposal(prop)
                if not gate_res.success:
                    logger.warning("MemoryGate rejected proposal: %s", gate_res.reason)

        return episode_record
