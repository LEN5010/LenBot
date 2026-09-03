import time
from typing import Optional
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState, ParticipationThread, ThreadStatus
from len_bot.cognition.models import ThreadTransition

THREAD_FADE_AFTER = 120.0
THREAD_CLOSE_AFTER = 300.0

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

            # Activate or establish ParticipationThread (Goal 2 & ADR-0004)
            if new_state.current_thread and new_state.current_thread.status != ThreadStatus.CLOSED:
                new_state.current_thread.status = ThreadStatus.ACTIVE
                new_state.current_thread.last_relevant_at = event.timestamp
                new_state.current_thread.last_relevant_event_id = event.id
                new_state.current_thread.intervening_messages = 0
            else:
                recent_others = [p for p in new_state.participants if p != bot_actor_id][-3:]
                topic = new_state.active_topic or "general"
                new_state.current_thread = ParticipationThread(
                    thread_id=f"th_{int(event.timestamp)}_{event.id[:6] if event.id else 'init'}",
                    scene_id=event.scene_id,
                    topic=topic,
                    participants=[bot_actor_id] + recent_others,
                    bot_role="participant",
                    status=ThreadStatus.ACTIVE,
                    started_at=event.timestamp,
                    last_relevant_event_id=event.id,
                    last_relevant_at=event.timestamp,
                    intervening_messages=0
                )

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

            # ParticipationThread lifecycle transitions (Goal 3 & ADR-0012, ADR-0027)
            if new_state.current_thread and new_state.current_thread.status != ThreadStatus.CLOSED:
                topic_words = [w for w in new_state.current_thread.topic.split() if len(w) > 1]
                has_topic_match = any(w in event.raw_text for w in topic_words) if topic_words else False
                is_truly_relevant = (
                    event.is_mention_bot
                    or event.is_reply_bot
                    or event.scene_id.startswith("private:")
                    or event.actor_id == bot_actor_id
                    or has_topic_match
                )

                time_since_rel = event.timestamp - new_state.current_thread.last_relevant_at

                if is_truly_relevant:
                    if time_since_rel > THREAD_CLOSE_AFTER:
                        new_state.current_thread.status = ThreadStatus.CLOSED
                    elif time_since_rel > THREAD_FADE_AFTER:
                        new_state.current_thread.status = ThreadStatus.FADING
                    else:
                        new_state.current_thread.status = ThreadStatus.ACTIVE

                    new_state.current_thread.last_relevant_at = event.timestamp
                    new_state.current_thread.last_relevant_event_id = event.id
                    new_state.current_thread.intervening_messages = 0
                    if event.actor_id and event.actor_id not in new_state.current_thread.participants:
                        new_state.current_thread.participants.append(event.actor_id)
                else:
                    # Participant-only or off-topic: count towards intervening without updating last_relevant_at
                    new_state.current_thread.intervening_messages += 1
                    if new_state.current_thread.intervening_messages >= 4 or time_since_rel > THREAD_CLOSE_AFTER:
                        new_state.current_thread.status = ThreadStatus.CLOSED
                    elif new_state.current_thread.intervening_messages >= 2 or time_since_rel > THREAD_FADE_AFTER:
                        new_state.current_thread.status = ThreadStatus.FADING

        # Incorporate typed social_state if passed in event metadata (Event -> State, ADR-0025)
        if "social_state" in event.metadata:
            s_state = event.metadata["social_state"]
            if "topic" in s_state and s_state["topic"] is not None:
                new_state.active_topic = s_state["topic"]
                if new_state.current_thread:
                    new_state.current_thread.topic = s_state["topic"]
            if "thread_transition" in s_state and s_state["thread_transition"] is not None:
                trans = s_state["thread_transition"]
                if trans == "close" or trans == ThreadTransition.CLOSE:
                    if new_state.current_thread:
                        new_state.current_thread.status = ThreadStatus.CLOSED
                    new_state.bot_engagement = "observing"
                elif trans == "fade" or trans == ThreadTransition.FADE:
                    if new_state.current_thread:
                        new_state.current_thread.status = ThreadStatus.FADING
                elif trans == "keep" or trans == ThreadTransition.KEEP:
                    if new_state.current_thread:
                        new_state.current_thread.status = ThreadStatus.ACTIVE

        # Also support legacy soft_annotation for backward-compatible manual events
        elif "soft_annotation" in event.metadata:
            anno = event.metadata["soft_annotation"]
            new_state.soft_annotations.update(anno)
            if "topic" in anno:
                new_state.active_topic = anno["topic"]
                if new_state.current_thread:
                    new_state.current_thread.topic = anno["topic"]
            if anno.get("close_thread"):
                if new_state.current_thread:
                    new_state.current_thread.status = ThreadStatus.CLOSED
                new_state.bot_engagement = "observing"
            elif "thread_status" in anno:
                if new_state.current_thread:
                    try:
                        new_state.current_thread.status = ThreadStatus(anno["thread_status"])
                    except ValueError:
                        pass

        return new_state
