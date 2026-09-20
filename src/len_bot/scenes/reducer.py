"""Only observable protocol facts enter the persistent session."""
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import OriginalCoverage, ParticipantFacts, SceneSession


class SceneReducer:
    @staticmethod
    def reduce(state: SceneSession | None, event: Event, bot_actor_id: str) -> SceneSession:
        result = state.model_copy(deep=True) if state else SceneSession(scene_id=event.scene_id)
        result.version += 1
        result.last_event_at = event.timestamp
        if (event.event_type == EventType.CONVERSATION_COMMITTED
                and not event.metadata.get('operator_control')
                and event.payload.get('output_kind', 'chat') == 'chat'):
            handled = {item['source_event_id'] for item in event.payload.get('source_outcomes', [])}
            from len_bot.runtime.attention import AttentionPolicy
            presented = event.payload.get('provided_original_ranges', {})
            remaining = []
            for wake in result.pending_wakes:
                if wake.event_id in handled:
                    continue
                if wake.event_id in presented:
                    coverage = OriginalCoverage.model_validate(presented[wake.event_id])
                    wake.observation = (wake.observation.merged_with(coverage)
                                        if wake.observation is not None else coverage)
                # Satisfying a read opportunity does not synthesize a silent
                # source outcome or complete an addressed request/work.
                if (wake.observation is not None and wake.observation.complete
                        and not AttentionPolicy._is_obligation(wake)):
                    continue
                remaining.append(wake)
            result.pending_wakes = remaining
            if 'next_observation_at' in event.payload:
                result.attention_sample_at = event.payload['next_observation_at']
            if 'observing_until' in event.payload:
                result.observing_until = event.payload['observing_until']
            for actor_id in event.payload.get('outcome',{}).get('release_focus_actor_ids',[]):
                result.focused_participants.pop(actor_id,None)
            wake = event.payload.get('outcome', {}).get('wake_decision')
            if wake and result.wake_confirmation and wake['request_event_id'] == result.wake_confirmation.request_event_id:
                if wake['decision'] == 'confirm':
                    from len_bot.runtime.sleep_policy import WAKE_SECONDS
                    result.awake_until = event.timestamp + WAKE_SECONDS
                    result.last_direct_human_at = event.timestamp
                    result.wake_source_event_id = wake['source_event_id']
                    result.wake_confirmation = None
                elif wake['decision'] == 'decline':
                    result.wake_confirmation = None
                elif wake['decision'] == 'ask':
                    result.wake_confirmation.prompt_commit_id = event.id
        elif event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}:
            if event.actor_id != bot_actor_id:
                result.consecutive_bot_messages = 0
                result.human_messages_since_bot += 1
                person = result.participants.get(event.actor_id) or ParticipantFacts(actor_id=event.actor_id)
                sender = event.payload.get('sender') or {}
                for field in ('nickname', 'card', 'role'):
                    if field in sender:
                        setattr(person, field, str(sender[field]) if sender[field] is not None else None)
                result.participants[event.actor_id] = person
        elif (event.event_type == EventType.MESSAGE_SENT
              and event.payload.get('wake_confirmation_request_id') and result.wake_confirmation
              and event.payload['wake_confirmation_request_id'] == result.wake_confirmation.request_event_id):
            from len_bot.runtime.attention import is_real_send
            if is_real_send(event, bot_actor_id):
                result.wake_confirmation.prompt_event_id = event.id
                result.wake_confirmation.prompted_at = event.timestamp
        elif (event.event_type == EventType.MESSAGE_SENT and event.actor_id == bot_actor_id
              and not event.metadata.get('conversation_excluded')):
            from len_bot.runtime.attention import is_real_send
            if is_real_send(event, bot_actor_id):
                result.last_bot_message_at = event.timestamp
                result.last_bot_message_event_id = event.id
                result.consecutive_bot_messages += 1
                result.human_messages_since_bot = 0
        return result
