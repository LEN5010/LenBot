import logging
import time
from typing import Any, Optional
from len_bot.events.store import EventStore
from len_bot.scenes.models import SceneState

logger = logging.getLogger(__name__)

class OpenLoopManager:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def get_active_loops(self, scene_id: str) -> list[dict[str, Any]]:
        return await self.event_store.get_active_open_loops(scene_id)

    async def resolve_loop(self, loop_id: str, scene_id: str) -> bool:
        """ADR-0018: resolve an ACTIVE loop in place, preserving target/intent/source for traceability."""
        loops = await self.event_store.get_active_open_loops(scene_id)
        target = next((l for l in loops if l["id"] == loop_id), None)
        if not target:
            logger.info("OpenLoop %s not found (or not active) in scene %s; nothing to resolve", loop_id, scene_id)
            return False
        target["status"] = "resolved"
        await self.event_store.save_open_loop(target)
        logger.info("OpenLoop %s resolved", loop_id)
        return True

    async def sweep_ttl_expiration(self) -> list[str]:
        """Dual-track GC: Absolute TTL cleanup."""
        now = time.time()
        expired_ids = await self.event_store.expire_open_loops(now)
        if expired_ids:
            logger.info("Expired %d open loops past TTL: %s", len(expired_ids), expired_ids)
        return expired_ids

    async def check_scene_decay(self, scene_state: SceneState) -> list[str]:
        """Dual-track GC: Scene activity decay (e.g. if topic moved on long ago)."""
        # If consecutive bot messages is 0 and scene has been quiet for > 12 hours
        now = time.time()
        loops = await self.event_store.get_active_open_loops(scene_state.scene_id)
        decayed = []
        for l in loops:
            created_at = l.get("created_at", now)
            # If open loop is older than 4 hours and not answered, decay it
            if (now - created_at) > 14400.0:
                await self.event_store.save_open_loop({
                    "id": l["id"],
                    "scene_id": scene_state.scene_id,
                    "target_actor_id": l["target_actor_id"],
                    "intent": l["intent"],
                    "source_event_id": l["source_event_id"],
                    "status": "decayed",
                    "created_at": l["created_at"],
                    "expires_at": l["expires_at"]
                })
                decayed.append(l["id"])
        return decayed
