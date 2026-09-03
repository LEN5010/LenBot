import logging
import time
import uuid
from typing import Optional, Tuple
from len_bot.events.store import EventStore
from len_bot.memory.models import MemoryProposal, MemoryItem, MemoryStatus
from len_bot.memory.store import MemoryStore
from len_bot.memory.writes import validate_memory_proposal, commit_memory_proposal_core

logger = logging.getLogger(__name__)

class MemoryGateResult:
    def __init__(self, success: bool, reason: str, memory_item: Optional[MemoryItem] = None):
        self.success = success
        self.reason = reason
        self.memory_item = memory_item

class MemoryGate:
    """Authoritative gate validating evidence provenance and managing conflict resolution (ADR-0011, ADR-0025)."""

    def __init__(self, memory_store: MemoryStore, event_store: EventStore):
        self.memory_store = memory_store
        self.event_store = event_store

    async def commit_proposal(self, proposal: MemoryProposal, scene_id: Optional[str] = None) -> MemoryGateResult:
        sid = scene_id or proposal.scope
        if not sid:
            return MemoryGateResult(False, "Rejected: Missing memory scope")

        async with self.memory_store.write_lock:
            try:
                await validate_memory_proposal(self.memory_store._db, proposal, sid)
                item = await commit_memory_proposal_core(self.memory_store._db, proposal, sid)
                await self.memory_store._db.commit()
                return MemoryGateResult(True, "Committed memory", item)
            except Exception as e:
                await self.memory_store._db.rollback()
                logger.warning("MemoryGate rejected proposal: %s", e)
                return MemoryGateResult(False, f"Rejected: {e}")
