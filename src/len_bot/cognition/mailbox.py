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
        self._cancelled: bool = False
        self._cancellation_reason: Optional[str] = None

    def post(self, event: Event) -> None:
        """Called by SceneActor worker when a new event arrives for this scene."""
        self._interim_events.append(event)
        
        # Check for urgent cancellation/steering keywords
        urgent_cancel_keywords = ["不用了", "不用查了", "算了", "取消", "闭嘴", "停"]
        if any(k in event.raw_text for k in urgent_cancel_keywords):
            self._cancelled = True
            self._cancellation_reason = f"User '{event.actor_id}' requested cancellation: {event.raw_text}"
        
        self._queue.put_nowait(event)

    def check_steering(self) -> Optional[SteeringSignal]:
        """Checked by Pi at ReAct step boundaries."""
        if self._cancelled:
            last_event = self._interim_events[-1] if self._interim_events else None
            return SteeringSignal(
                steering_type=SteeringType.CANCEL,
                source_event=last_event,
                reason=self._cancellation_reason or "Episode cancelled by steering"
            )
        return None

    def get_interim_events(self) -> list[Event]:
        return list(self._interim_events)
