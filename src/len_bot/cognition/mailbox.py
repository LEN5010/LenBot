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
        # ADR-0029: burst event that triggered this episode; attached to dependent open loops.
        self.origin_stimulus_id = origin_stimulus_id
        self.origin_mode = "live"
        self.source_started_at: float | None = None
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._interim_events: list[Event] = []
        self._unconsumed_follow_ups: list[Event] = []
        self._cancelled: bool = False
        self._cancellation_reason: Optional[str] = None
        self._cursor: int = 0

    def post(self, event: Event) -> None:
        """Called by SceneActor worker when a new event arrives for this scene.
        ADR-0026: Only accepts conversational messages (GROUP_MESSAGE_RECEIVED / PRIVATE_MESSAGE_RECEIVED).
        Internal, state, task, and sensor fact events are discarded.
        """
        allowed = {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}
        if event.event_type not in allowed:
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
        """Destructive method: ONLY consumed by ReActAgentCore during cognition ReAct steps."""
        consumed = list(self._unconsumed_follow_ups)
        self._unconsumed_follow_ups.clear()
        return consumed

    def has_unseen_interim(self) -> bool:
        """Non-destructive query (ADR-0026, §6): returns True if unread interim events exist."""
        return len(self._interim_events) > self._cursor

    def fetch_unseen_interim_events(self) -> list[Event]:
        """Non-destructive query advancing read cursor: returns events arrived since last check."""
        unseen = self._interim_events[self._cursor:]
        self._cursor = len(self._interim_events)
        return unseen

    def get_interim_events(self) -> list[Event]:
        return list(self._interim_events)
