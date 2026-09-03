import asyncio
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
from len_bot.events.models import Event

class SteeringType(StrEnum):
    CANCEL = "CANCEL"
    FOLLOW_UP = "FOLLOW_UP"

class SteeringSignal(BaseModel):
    steering_type: SteeringType
    source_event: Event
    reason: str

class EpisodeMailbox:
    def __init__(self, episode_id: str, scene_id: str, base_scene_version: int):
        self.episode_id = episode_id
        self.scene_id = scene_id
        self.base_scene_version = base_scene_version
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._interim_events: list[Event] = []
        self._unconsumed_follow_ups: list[Event] = []
        self._cancelled: bool = False
        self._cancellation_reason: Optional[str] = None
        self._cursor: int = 0

    def post(self, event: Event) -> None:
        """Called by SceneActor worker when a new event arrives for this scene."""
        self._interim_events.append(event)
        
        # Check for urgent cancellation/steering keywords
        urgent_cancel_keywords = ["不用了", "不用查了", "算了", "取消", "闭嘴", "停"]
        if any(k in event.raw_text for k in urgent_cancel_keywords):
            self._cancelled = True
            self._cancellation_reason = f"User '{event.actor_id}' requested cancellation: {event.raw_text}"
        elif event.is_mention_bot or event.is_reply_bot or event.event_type.value == "PRIVATE_MESSAGE_RECEIVED":
            self._unconsumed_follow_ups.append(event)
        
        self._queue.put_nowait(event)

    def is_cancelled(self) -> bool:
        """Non-destructive query: returns True if episode has been cancelled."""
        return self._cancelled

    def cancellation_reason(self) -> Optional[str]:
        return self._cancellation_reason

    def has_follow_up(self) -> bool:
        """Non-destructive query: returns True if unconsumed follow-ups are pending."""
        return bool(self._unconsumed_follow_ups)

    def consume_follow_ups(self) -> list[Event]:
        """Destructive method: ONLY consumed by PiAgentCore during cognition ReAct steps."""
        consumed = list(self._unconsumed_follow_ups)
        self._unconsumed_follow_ups.clear()
        return consumed

    def fetch_unseen_interim_events(self) -> list[Event]:
        """Non-destructive query advancing read cursor: returns events arrived since last check."""
        unseen = self._interim_events[self._cursor:]
        self._cursor = len(self._interim_events)
        return unseen

    def get_interim_events(self) -> list[Event]:
        return list(self._interim_events)
