import time
from typing import Optional, Any
from len_bot.events.models import Stimulus, StimulusType
from len_bot.scenes.models import SceneState
from len_bot.attention.models import AttentionResult, AttentionDisposition
from len_bot.config import RuntimeConfig

from len_bot.state.interest import InterestModel
from len_bot.attention.budget import SpeakingBudget
from len_bot.attention.initiative import InitiativeEngine, InitiativeDisposition

class AttentionEngine:
    def __init__(
        self,
        config: RuntimeConfig,
        interest_model: Optional[InterestModel] = None,
        speaking_budget: Optional[SpeakingBudget] = None
    ):
        self.config = config
        self.interest_model = interest_model or InterestModel()
        self.speaking_budget = speaking_budget or SpeakingBudget(base_threshold=0.60)
        self.initiative_engine = InitiativeEngine(self.interest_model, self.speaking_budget)

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
        activity = scene_state.activity_level if scene_state else "quiet"
        last_bot_at = scene_state.recent_bot_message_at if scene_state else None

        # Check Active Engagement continuation (§34 & §106)
        if scene_state and scene_state.bot_engagement == "active" and activity != "hot":
            if last_bot_at and (now - last_bot_at) > 1.0:
                return AttentionResult(
                    disposition=AttentionDisposition.WAKE,
                    reason="active_conversation_engagement"
                )

        # Check Initiative Engine (§83-86 & ADR-0012)
        init_disp, init_reason, init_score = self.initiative_engine.evaluate(text, scene_state, now)
        if init_disp == InitiativeDisposition.WAKE_FOR_INITIATIVE:
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason=init_reason
            )
        elif init_disp == InitiativeDisposition.RETAIN_FOR_LATER:
            return AttentionResult(
                disposition=AttentionDisposition.TRACK,
                reason=init_reason,
                soft_annotation={"initiative_interest": init_score}
            )

        # Default fallback: OBSERVE
        return AttentionResult(
            disposition=AttentionDisposition.OBSERVE,
            reason="unrelated_social_observation"
        )
