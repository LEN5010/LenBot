import logging
import time
from typing import Optional, Callable, Awaitable
from len_bot.events.models import Event
from len_bot.memory.models import EpisodeRecord, MemoryProposal, MemoryCertainty
from len_bot.memory.store import MemoryStore
from len_bot.memory.gate import MemoryGate

logger = logging.getLogger(__name__)

class ReflectionEngine:
    """Produces L1 Episode Records and L2 Social Memory proposals from experiences (ADR-0011)."""

    def __init__(
        self,
        memory_store: MemoryStore,
        memory_gate: MemoryGate,
        llm_reflector: Optional[Callable[[list[Event]], Awaitable[tuple[EpisodeRecord, list[MemoryProposal]]]]] = None
    ):
        self.memory_store = memory_store
        self.memory_gate = memory_gate
        self.llm_reflector = llm_reflector

    async def run_micro_reflection(self, scene_id: str, events: list[Event]) -> Optional[EpisodeRecord]:
        """Runs micro-reflection when a conversation block has completed."""
        if not events:
            return None

        # 1. Generate Episode Record (L1) and Memory Proposals (L2)
        if self.llm_reflector:
            episode_record, proposals = await self.llm_reflector(events)
        else:
            # Deterministic heuristic fallback when no LLM reflector provided
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
            proposals = []

        # 2. Persist L1 Episode
        await self.memory_store.save_episode(episode_record)
        logger.info("Saved L1 Episode %s: %s", episode_record.id, episode_record.title)

        # 3. Commit L2 Memory Proposals through Memory Gate (P0.3)
        for prop in proposals:
            prop.scope = scene_id
            gate_res = await self.memory_gate.commit_proposal(prop)
            if not gate_res.success:
                logger.warning("MemoryGate rejected proposal: %s", gate_res.reason)

        return episode_record
