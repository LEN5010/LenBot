from typing import Optional

class EpisodeMailbox:
    """Episode identity, interaction participants, and explicit cancellation."""

    def __init__(self, episode_id: str, scene_id: str, base_scene_version: int,
                 origin_stimulus_id: Optional[str] = None):
        self.episode_id = episode_id
        self.scene_id = scene_id
        self.base_scene_version = base_scene_version
        # The real triggering event also owns any resulting wait for a reply.
        self.origin_stimulus_id = origin_stimulus_id
        self.origin_mode = "live"
        self.output_kind = 'chat'
        self.plugin_origin = None
        self.mention_all = False
        self.requester_qq_uid: str | None = None
        self.command_id: str | None = None
        self.announcement_member: str | None = None
        self.source_started_at: float | None = None
        self.interaction_actors: set[str] = set()
        self.handled_source_ids: set[str] = set()
        self.messages_committed = 0
        self.next_checkpoint = 0
        self._cancelled: bool = False
        self._cancellation_reason: Optional[str] = None

    def cancel(self, reason: str = "Explicitly cancelled") -> None:
        """Explicitly cancels this episode."""
        self._cancelled = True
        self._cancellation_reason = reason

    def is_cancelled(self) -> bool:
        """Non-destructive query: returns True if episode has been cancelled."""
        return self._cancelled

    def cancellation_reason(self) -> Optional[str]:
        return self._cancellation_reason
