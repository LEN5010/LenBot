import logging
import time
import uuid
from typing import Optional, Tuple
from len_bot.events.store import EventStore
from len_bot.memory.models import MemoryProposal, MemoryItem, MemoryStatus
from len_bot.memory.store import MemoryStore

logger = logging.getLogger(__name__)

class MemoryGateResult:
    def __init__(self, success: bool, reason: str, memory_item: Optional[MemoryItem] = None):
        self.success = success
        self.reason = reason
        self.memory_item = memory_item

class MemoryGate:
    """Authoritative gate validating evidence provenance and managing conflict resolution (ADR-0011)."""

    def __init__(self, memory_store: MemoryStore, event_store: EventStore):
        self.memory_store = memory_store
        self.event_store = event_store

    async def commit_proposal(self, proposal: MemoryProposal) -> MemoryGateResult:
        # 1. Evidence Provenance Validation (§56 & §60)
        if not proposal.evidence:
            return MemoryGateResult(False, "Rejected: Memory proposal lacks evidence references")

        # 2. Scope Validation (§61 & P0.3)
        if not proposal.scope:
            return MemoryGateResult(False, "Rejected: Missing memory scope")

        # Verify evidence existence in SQLite within proposal.scope (P0.3)
        placeholders = ",".join("?" for _ in proposal.evidence)
        cursor = await self.event_store._db.execute(
            f"SELECT COUNT(*) FROM events WHERE id IN ({placeholders}) AND scene_id = ?;",
            [*proposal.evidence, proposal.scope]
        )
        (event_count,) = await cursor.fetchone()
        
        # Also check episodes table if evidence might be an episode ID within proposal.scope
        ep_cursor = await self.memory_store._db.execute(
            f"SELECT COUNT(*) FROM episodes WHERE id IN ({placeholders}) AND scene_id = ?;",
            [*proposal.evidence, proposal.scope]
        )
        (ep_count,) = await ep_cursor.fetchone()

        required_evidence_count = len(set(proposal.evidence))
        if (event_count + ep_count) < required_evidence_count:
            return MemoryGateResult(
                False,
                f"Rejected: Evidence integrity check failed. Expected {required_evidence_count} evidence items in scope {proposal.scope}, found {event_count + ep_count}"
            )

        # 3. Conflict Resolution on Semantic Slot (§57)
        now = time.time()
        existing = await self.memory_store.find_active_memory(
            subject=proposal.subject,
            kind=proposal.kind,
            key=proposal.key,
            scope=proposal.scope
        )

        new_item_id = f"mem_{uuid.uuid4().hex[:10]}"
        if existing:
            if existing.value == proposal.value:
                # Value unchanged: confirm and update timestamp
                existing.last_confirmed_at = now
                existing.evidence = list(set(existing.evidence + proposal.evidence))
                await self.memory_store.save_memory(existing)
                logger.info("Confirmed existing memory %s (%s)", existing.id, existing.human_readable_assertion)
                return MemoryGateResult(True, "Confirmed existing memory value", existing)
            else:
                # Value conflict! Supersede old memory and point to new memory ID (§57 & ADR-0011)
                await self.memory_store.update_memory_status(existing.id, MemoryStatus.SUPERSEDED, superseded_by=new_item_id)
                logger.info("Memory conflict: superseded old memory %s (was '%s', superseded_by='%s')",
                            existing.id, existing.value, new_item_id)

        # 4. Insert new active memory
        new_item = MemoryItem(
            id=new_item_id,
            subject=proposal.subject,
            kind=proposal.kind,
            key=proposal.key,
            value=proposal.value,
            temporal=proposal.temporal,
            certainty=proposal.certainty,
            scope=proposal.scope,
            visibility=proposal.visibility,
            evidence=proposal.evidence,
            status=MemoryStatus.ACTIVE,
            human_readable_assertion=proposal.human_readable_assertion,
            created_at=now,
            last_confirmed_at=now
        )
        await self.memory_store.save_memory(new_item)
        logger.info("Committed new active memory %s: %s", new_item.id, new_item.human_readable_assertion)
        return MemoryGateResult(True, "Committed new memory", new_item)
