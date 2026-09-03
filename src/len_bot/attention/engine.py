import time
from typing import Optional, Any
from len_bot.events.models import Stimulus, StimulusType
from len_bot.scenes.models import SceneState
from len_bot.attention.models import AttentionResult, AttentionDisposition
from len_bot.config import RuntimeConfig

class AttentionEngine:
    def __init__(self, config: RuntimeConfig):
        self.config = config

    def evaluate(
        self,
        stimulus: Stimulus,
        scene_state: Optional[SceneState],
        active_open_loops: list[dict[str, Any]]
    ) -> AttentionResult:
        now = time.time()

        # --- Layer 1: Hard Attention (Deterministic) ---
        if stimulus.stimulus_type == StimulusType.PROACTIVE_TASK:
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason="proactive_task_due"
            )

        if stimulus.scene_id.startswith("private:"):
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason="private_message"
            )

        if stimulus.has_mention_bot:
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason="explicit_mention_bot"
            )

        if stimulus.has_reply_bot:
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason="reply_to_bot"
            )

        # Check Active Open Loops matching speaker
        for loop in active_open_loops:
            if loop.get("target_actor_id") == stimulus.actor_id and loop.get("status") == "active":
                return AttentionResult(
                    disposition=AttentionDisposition.WAKE,
                    reason=f"active_open_loop_response:{loop.get('intent', 'unknown')}"
                )

        # --- Layer 2: Heuristic Attention (State-Dependent) ---
        text = stimulus.combined_text
        matched_keywords = [k for k in self.config.monitored_keywords if k in text]

        activity = scene_state.activity_level if scene_state else "quiet"
        last_bot_at = scene_state.recent_bot_message_at if scene_state else None

        # Check Active Engagement continuation (§34 & §106)
        # If Bot is already an active participant in this scene's conversation:
        if scene_state and scene_state.bot_engagement == "active" and activity != "hot":
            # Avoid waking immediately on Bot's own sent echo
            if last_bot_at and (now - last_bot_at) > 1.0:
                return AttentionResult(
                    disposition=AttentionDisposition.WAKE,
                    reason="active_conversation_engagement"
                )

        if matched_keywords:
            # Check scene activity & backpressure
            if activity == "hot":
                # High-traffic group: apply backpressure, do not wake
                return AttentionResult(
                    disposition=AttentionDisposition.OBSERVE,
                    reason="backpressure_scene_hot",
                    soft_annotation={"topic_hint": matched_keywords[0]}
                )

            # Check Bot speaking cooldown
            if last_bot_at and (now - last_bot_at) < self.config.bot_cooldown_seconds:
                # Cooldown active: annotate soft topic but don't wake
                return AttentionResult(
                    disposition=AttentionDisposition.TRACK,
                    reason="cooldown_active_track_only",
                    soft_annotation={"topic_hint": matched_keywords[0]}
                )

            # Passed cooldown and keyword probe: WAKE
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason=f"keyword_probe_matched:{','.join(matched_keywords)}"
            )

        # Default fallback: OBSERVE
        return AttentionResult(
            disposition=AttentionDisposition.OBSERVE,
            reason="unrelated_social_observation"
        )
