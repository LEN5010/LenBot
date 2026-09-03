from enum import StrEnum
from pydantic import BaseModel, Field
from typing import Optional, Any
import time

class ThreadStatus(StrEnum):
    ACTIVE = "active"
    FADING = "fading"
    CLOSED = "closed"

class ParticipationThread(BaseModel):
    thread_id: str
    scene_id: str
    topic: str = "general"
    participants: list[str] = Field(default_factory=list)
    bot_role: str = "participant"  # participant, observer, answerer, organizer
    status: ThreadStatus = ThreadStatus.ACTIVE
    started_at: float = Field(default_factory=time.time)
    last_relevant_event_id: str = ""
    last_relevant_at: float = Field(default_factory=time.time)
    intervening_messages: int = 0
    relevance_confidence: float = 1.0
    origin_episode_id: Optional[str] = None

class SceneState(BaseModel):
    scene_id: str
    version: int = 0
    participants: list[str] = Field(default_factory=list)
    activity_level: str = "quiet"  # quiet, active, hot
    active_topic: Optional[str] = None
    bot_engagement: str = "idle"   # idle, observing, active
    current_thread: Optional[ParticipationThread] = None
    recent_bot_message_at: Optional[float] = None
    consecutive_bot_messages: int = 0
    intervening_messages_since_bot: int = 0
    soft_annotations: dict[str, Any] = Field(default_factory=dict)
    last_event_at: float = Field(default_factory=time.time)
    recent_event_timestamps: list[float] = Field(default_factory=list)

    def record_participant(self, actor_id: str) -> None:
        if actor_id not in self.participants:
            self.participants.append(actor_id)
            if len(self.participants) > 100:
                self.participants.pop(0)

    def update_activity(self, now: float) -> None:
        # Keep timestamps in the last 60 seconds
        cutoff = now - 60.0
        self.recent_event_timestamps = [t for t in self.recent_event_timestamps if t >= cutoff]
        self.recent_event_timestamps.append(now)
        
        count = len(self.recent_event_timestamps)
        if count >= 15:
            self.activity_level = "hot"
        elif count >= 4:
            self.activity_level = "active"
        else:
            self.activity_level = "quiet"
