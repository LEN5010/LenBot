"""Offline Social Core replay with no action execution authority."""

from typing import Any, Optional

from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.cognition.session import GroupAgentSession, GroupAgentSessionReducer


class ReplayLab:
    def __init__(
        self,
        config: RuntimeConfig,
        social_core: Any,
    ):
        self.config = config
        self.social_core = social_core

    @staticmethod
    def _to_stimulus(event: Event) -> Optional[Stimulus]:
        if event.event_type in (EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED):
            return Stimulus(
                scene_id=event.scene_id,
                stimulus_type=StimulusType.SINGLE_MESSAGE,
                source_event_ids=[event.id],
                actor_id=event.actor_id,
                combined_text=event.raw_text,
                has_mention_bot=event.is_mention_bot,
                has_reply_bot=event.is_reply_bot,
                timestamp=event.timestamp,
            )
        if event.event_type == EventType.TASK_DUE:
            return Stimulus(
                scene_id=event.scene_id,
                stimulus_type=StimulusType.PROACTIVE_TASK,
                source_event_ids=[event.id],
                actor_id=event.actor_id,
                combined_text=event.raw_text,
                timestamp=event.timestamp,
            )
        if event.event_type in (EventType.LIVE_STARTED, EventType.LIVE_ENDED):
            return Stimulus(
                scene_id=event.scene_id,
                stimulus_type=StimulusType.PLUGIN_FACT,
                source_event_ids=[event.id],
                actor_id=event.actor_id,
                combined_text=event.raw_text,
                timestamp=event.timestamp,
            )
        return None

    async def run(self, events: list[Event]) -> list[dict]:
        sessions: dict[str, GroupAgentSession] = {}
        raw_by_scene: dict[str, list[Event]] = {}
        rows: list[dict] = []
        bot_actor_id = f"user:{self.config.bot_qq}"

        for event in sorted(events, key=lambda e: e.timestamp):
            session = sessions.setdefault(event.scene_id, GroupAgentSession(scene_id=event.scene_id))
            session = GroupAgentSessionReducer.reduce(session, event, bot_actor_id)
            sessions[event.scene_id] = session
            raw_by_scene.setdefault(event.scene_id, []).append(event)

            stimulus = self._to_stimulus(event)
            if stimulus is None:
                continue

            result, trace = await self.social_core.execute(
                session=session.model_copy(deep=True),
                burst=stimulus,
                raw_events=raw_by_scene[event.scene_id][-100:],
                active_open_loops=[],
                pending_next_wake=None,
            )
            session = GroupAgentSessionReducer.apply_cognition(session, result, len(raw_by_scene[event.scene_id]))
            sessions[event.scene_id] = session
            row = {
                "event_id": event.id,
                "timestamp": event.timestamp,
                "actor_id": event.actor_id,
                "text": event.raw_text[:120],
                "decision": result.decision.action.value,
                "reason": result.decision.reason,
                "understanding": result.perception.summary,
                "would_send": [p.content for p in result.message_proposals],
                "trace": trace,
            }
            rows.append(row)
        return rows
