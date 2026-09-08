"""Only observable protocol facts enter the persistent session."""
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import ParticipantFacts, SceneSession


class SceneReducer:
    @staticmethod
    def reduce(state: SceneSession | None, event: Event, bot_actor_id: str) -> SceneSession:
        result = state.model_copy(deep=True) if state else SceneSession(scene_id=event.scene_id)
        result.version += 1
        result.last_event_at = event.timestamp
        if event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}:
            if event.actor_id != bot_actor_id:
                result.consecutive_bot_messages = 0
                result.human_messages_since_bot += 1
                person = result.participants.get(event.actor_id) or ParticipantFacts(actor_id=event.actor_id)
                sender = event.payload.get('sender') or {}
                for field in ('nickname', 'card', 'role'):
                    if field in sender:
                        setattr(person, field, str(sender[field]) if sender[field] is not None else None)
                result.participants[event.actor_id] = person
        elif (event.event_type == EventType.MESSAGE_SENT and event.actor_id == bot_actor_id
              and not event.metadata.get('conversation_excluded')):
            result.last_bot_message_at = event.timestamp
            result.last_bot_message_event_id = event.id
            result.consecutive_bot_messages += 1
            result.human_messages_since_bot = 0
        return result
