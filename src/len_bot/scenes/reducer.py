import time
from typing import Optional
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState

class SceneReducer:
    @staticmethod
    def reduce(state: Optional[SceneState], event: Event, bot_actor_id: str) -> SceneState:
        if state is None:
            new_state = SceneState(scene_id=event.scene_id, version=0)
        else:
            new_state = state.model_copy(deep=True)

        # Increment version for every valid event processed
        new_state.version += 1
        new_state.last_event_at = event.timestamp

        if event.event_type == EventType.MESSAGE_SENT:
            new_state.recent_bot_message_at = event.timestamp
            new_state.consecutive_bot_messages += 1
            new_state.bot_engagement = "active"
            new_state.intervening_messages_since_bot = 0
            new_state.record_participant(bot_actor_id)
        elif event.event_type in (EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED):
            new_state.consecutive_bot_messages = 0
            if event.actor_id != bot_actor_id:
                new_state.intervening_messages_since_bot += 1
            if event.actor_id:
                new_state.record_participant(event.actor_id)
            new_state.update_activity(event.timestamp)

            # Natural engagement lifecycle decay (§34 & P1)
            time_since_bot = (event.timestamp - new_state.recent_bot_message_at) if new_state.recent_bot_message_at else 999999.0
            if new_state.bot_engagement == "active":
                if new_state.intervening_messages_since_bot >= 5 or time_since_bot > 1800.0:
                    new_state.bot_engagement = "observing"
            elif new_state.bot_engagement == "observing":
                if new_state.intervening_messages_since_bot >= 15 or time_since_bot > 3600.0:
                    new_state.bot_engagement = "idle"

        # Incorporate soft annotations if passed in event metadata
        if "soft_annotation" in event.metadata:
            new_state.soft_annotations.update(event.metadata["soft_annotation"])
            if "topic" in event.metadata["soft_annotation"]:
                new_state.active_topic = event.metadata["soft_annotation"]["topic"]

        return new_state
