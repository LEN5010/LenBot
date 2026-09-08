import asyncio
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
from len_bot.events.models import Event, EventType

class SteeringType(StrEnum):
    CANCEL = "CANCEL"
    FOLLOW_UP = "FOLLOW_UP"

class SteeringSignal(BaseModel):
    steering_type: SteeringType
    source_event: Event
    reason: str

class EpisodeMailbox:
    def __init__(self, episode_id: str, scene_id: str, base_scene_version: int,
                 origin_stimulus_id: Optional[str] = None):
        self.episode_id = episode_id
        self.scene_id = scene_id
        self.base_scene_version = base_scene_version
        # The real triggering event also owns any resulting wait for a reply.
        self.origin_stimulus_id = origin_stimulus_id
        self.origin_mode = "live"
        self.output_kind = 'chat'
        self.requester_qq_uid: str | None = None
        self.command_id: str | None = None
        self.announcement_member: str | None = None
        self.source_started_at: float | None = None
        self.interaction_actors: set[str] = set()
        self.initial_observed_rowid: int | None = None
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._interim_events: list[Event] = []
        self._unconsumed_follow_ups: list[Event] = []
        self._cancelled: bool = False
        self._cancellation_reason: Optional[str] = None
        self._cursor: int = 0
        self._acknowledged_ids: set[str] = set()

    def post(self, event: Event) -> None:
        """Called by SceneActor worker when a new event arrives for this scene.
        Only accepts group/private messages admitted to ordinary conversation.
        Internal, state, task, and sensor fact events are discarded.
        """
        allowed = {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}
        if event.event_type not in allowed or event.metadata.get('conversation_excluded'):
            return

        self._interim_events.append(event)
        
        # Natural-language intent belongs to cognition; only cancel() has authority.
        self._queue.put_nowait(event)

    def cancel(self, reason: str = "Explicitly cancelled") -> None:
        """Explicitly cancels this episode."""
        self._cancelled = True
        self._cancellation_reason = reason

    def is_cancelled(self) -> bool:
        """Non-destructive query: returns True if episode has been cancelled."""
        return self._cancelled

    def cancellation_reason(self) -> Optional[str]:
        return self._cancellation_reason

    def has_follow_up(self) -> bool:
        """Non-destructive query: returns True if unconsumed follow-ups are pending."""
        return bool(self._unconsumed_follow_ups)

    def consume_follow_ups(self) -> list[Event]:
        """Return and clear follow-up events already selected by the current episode."""
        consumed = list(self._unconsumed_follow_ups)
        self._unconsumed_follow_ups.clear()
        return consumed

    def has_unseen_interim(self) -> bool:
        """Whether newly arrived conversation input remains unread."""
        return len(self._interim_events) > self._cursor

    def fetch_unseen_interim_events(self) -> list[Event]:
        """Non-destructive query advancing read cursor: returns events arrived since last check."""
        unseen = self._interim_events[self._cursor:]
        self._cursor = len(self._interim_events)
        return unseen

    def acknowledge_through(self, through_rowid: int) -> None:
        """Acknowledge only contiguous events actually included in model input."""
        while self._cursor < len(self._interim_events):
            rowid = self._interim_events[self._cursor].metadata.get("_rowid")
            if rowid is None or rowid > through_rowid:
                break
            self._cursor += 1

    def acknowledge_events(self, event_ids) -> None:
        """Advance only across fully read originals, leaving unselected holes."""
        self._acknowledged_ids.update(event_ids)
        while self._cursor < len(self._interim_events) and self._interim_events[self._cursor].id in self._acknowledged_ids:
            self._cursor += 1

    def get_interim_events(self) -> list[Event]:
        return list(self._interim_events)
