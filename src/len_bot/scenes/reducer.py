import time
from typing import Optional
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState

class SceneReducer:
    @staticmethod
    def reduce(state: Optional[SceneState], event: Event, bot_actor_id: str) -> SceneState:
        if state is None:
            state = SceneState(scene_id=event.scene_id, version=0)

        # Increment version for every valid event processed
        state.version += 1
        state.last_event_at = event.timestamp

        if event.actor_id == bot_actor_id or event.event_type == EventType.MESSAGE_SENT:
            state.recent_bot_message_at = event.timestamp
            state.consecutive_bot_messages += 1
            state.bot_engagement = "active"
            state.record_participant(bot_actor_id)
        else:
            state.consecutive_bot_messages = 0
            if event.actor_id:
                state.record_participant(event.actor_id)
            state.update_activity(event.timestamp)

        # Incorporate soft annotations if passed in event metadata
        if "soft_annotation" in event.metadata:
            state.soft_annotations.update(event.metadata["soft_annotation"])
            if "topic" in event.metadata["soft_annotation"]:
                state.active_topic = event.metadata["soft_annotation"]["topic"]

        return state
