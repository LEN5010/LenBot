"""Standalone ledger transaction boundary for explicit management operations."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from len_bot.memory.models import MemoryItem, MemoryProposal
from len_bot.memory.store import MemoryStore
from len_bot.memory.writes import commit_memory_proposal_core, validate_memory_proposal

if TYPE_CHECKING:
    from len_bot.events.store import EventStore


@dataclass
class MemoryGateResult:
    success: bool
    reason: str
    memory_item: MemoryItem | None = None


class MemoryGate:
    """Normal cognitive turns use EventStore's combined proposal transaction."""

    def __init__(self, memory_store: MemoryStore, event_store: "EventStore", *, bot_actor_id: str):
        self.memory_store = memory_store
        self.event_store = event_store
        self.bot_actor_id = bot_actor_id

    async def commit_proposal(
        self, proposal: MemoryProposal, scene_id: str, *, through_rowid: int | None = None,
        revision_event_id: str | None = None,
    ) -> MemoryGateResult:
        async with self.memory_store.write_lock:
            try:
                await self.memory_store._db.execute("BEGIN IMMEDIATE")
                await validate_memory_proposal(
                    self.memory_store._db, proposal, scene_id, through_rowid, bot_actor_id=self.bot_actor_id,
                )
                item = await commit_memory_proposal_core(
                    self.memory_store._db, proposal, scene_id, self.memory_store.clock(),
                    revision_event_id=revision_event_id,
                )
                await self.memory_store._db.commit()
                return MemoryGateResult(True, "Committed memory", item)
            except Exception as error:
                await self.memory_store._db.rollback()
                return MemoryGateResult(False, f"Rejected: {error}")
