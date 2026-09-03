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
            state.intervening_messages_since_bot = 0
            state.record_participant(bot_actor_id)
        else:
            state.consecutive_bot_messages = 0
            state.intervening_messages_since_bot += 1
            if event.actor_id:
                state.record_participant(event.actor_id)
            state.update_activity(event.timestamp)

            # Natural engagement lifecycle decay (§34 & P1)
            time_since_bot = (event.timestamp - state.recent_bot_message_at) if state.recent_bot_message_at else 999999.0
            if state.bot_engagement == "active":
                if state.intervening_messages_since_bot >= 5 or time_since_bot > 1800.0:
                    state.bot_engagement = "observing"
            elif state.bot_engagement == "observing":
                if state.intervening_messages_since_bot >= 15 or time_since_bot > 3600.0:
                    state.bot_engagement = "idle"

        # Incorporate soft annotations if passed in event metadata
        if "soft_annotation" in event.metadata:
            state.soft_annotations.update(event.metadata["soft_annotation"])
            if "topic" in event.metadata["soft_annotation"]:
                state.active_topic = event.metadata["soft_annotation"]["topic"]

        return state
