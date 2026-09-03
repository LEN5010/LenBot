"""Replay Lab (ADR-0022, V2 plan §三十一).

Deterministic offline replay of a recorded event window: each message becomes a
stimulus, SceneState is materialized with the pure SceneReducer, and the real
AttentionEngine evaluates per-message dispositions — no sleeps, no network.
Optional cognition mock resolves WAKE into SILENCE/ACTION rows. Multiple
override sets allow Policy A vs Policy B comparison.
"""

from typing import Any, Callable, Awaitable, Optional

from len_bot.config import RuntimeConfig
from len_bot.attention.engine import AttentionEngine
from len_bot.attention.models import AttentionDisposition
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.scenes.reducer import SceneReducer
from len_bot.scenes.models import SceneState

_APPLIABLE_OVERRIDES = {
    "monitored_keywords",
}


def _apply_overrides(config: RuntimeConfig, overrides: Optional[dict]) -> tuple[RuntimeConfig, dict]:
    cfg = config.model_copy(deep=True)
    budget_threshold = None
    if overrides:
        for key, value in overrides.items():
            if key in _APPLIABLE_OVERRIDES:
                setattr(cfg, key, value)
            elif key == "speaking_budget_base_threshold":
                budget_threshold = value
    return cfg, {"speaking_budget_base_threshold": budget_threshold}


class ReplayLab:
    def __init__(
        self,
        config: RuntimeConfig,
        mock_cognition: Optional[Callable[[Stimulus], Awaitable[Any]]] = None,
    ):
        self.config = config
        self.mock_cognition = mock_cognition

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

    async def run(self, events: list[Event], overrides: Optional[dict] = None) -> list[dict]:
        cfg, extra = _apply_overrides(self.config, overrides)
        attention = AttentionEngine(cfg)
        if extra.get("speaking_budget_base_threshold") is not None:
            attention.speaking_budget.base_threshold = extra["speaking_budget_base_threshold"]

        state: Optional[SceneState] = None
        rows: list[dict] = []
        bot_actor_id = f"user:{self.config.bot_qq}"

        for event in sorted(events, key=lambda e: e.timestamp):
            # Materialize durable state first (pure reducer, no persistence)
            state = SceneReducer.reduce(state, event, bot_actor_id)

            stimulus = self._to_stimulus(event)
            if stimulus is None:
                continue

            att = attention.evaluate(stimulus, state, [], now=event.timestamp)
            row = {
                "event_id": event.id,
                "timestamp": event.timestamp,
                "actor_id": event.actor_id,
                "text": event.raw_text[:120],
                "disposition": att.disposition.value.lower(),
                "reason": att.reason,
            }

            if att.disposition == AttentionDisposition.WAKE and self.mock_cognition:
                cognition = await self.mock_cognition(stimulus)
                row["cognition"] = str(cognition)
            elif att.disposition == AttentionDisposition.WAKE:
                row["cognition"] = "WAKE (no cognition mock: episode would run)"

            rows.append(row)
        return rows
