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
            new_state.intervening_messages_since_bot = 0
            new_state.record_participant(bot_actor_id)

        elif event.event_type in (EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED):
            new_state.consecutive_bot_messages = 0
            if event.actor_id != bot_actor_id:
                new_state.intervening_messages_since_bot += 1
            if event.actor_id:
                new_state.record_participant(event.actor_id)
            new_state.update_activity(event.timestamp)

        return new_state
