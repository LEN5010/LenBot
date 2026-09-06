"""Read actual unanswered messages and expire them by their recorded deadline."""

import logging
from typing import Any

from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)


class OpenLoopManager:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def get_active_loops(self, scene_id: str) -> list[dict[str, Any]]:
        return await self.event_store.get_active_open_loops(scene_id)

    async def sweep_ttl_expiration(self) -> list[str]:
        """Expiry follows the stored deadline; no inferred topic/relationship decay."""
        expired = await self.event_store.expire_open_loops(self.event_store.clock())
        if expired:
            logger.info("Expired %d open loops at their stored deadline", len(expired))
        return expired
