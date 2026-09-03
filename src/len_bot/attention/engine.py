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
        self.monitored_keywords: list[str] = list(self.config.monitored_keywords) if self.config.monitored_keywords else []
        self.interest_model = interest_model or InterestModel()
        if self.monitored_keywords:
            self.interest_model.topic_keywords["monitored"] = list(self.monitored_keywords)
            self.interest_model.topics["monitored"] = 0.90
        self.speaking_budget = speaking_budget or SpeakingBudget(base_threshold=0.60)
        self.initiative_engine = InitiativeEngine(self.interest_model, self.speaking_budget)

    def update_monitored_keywords(self, keywords: list[str]) -> None:
        """ADR-0031, §23.1: Live hot-reload of monitored keywords in AttentionEngine."""
        self.monitored_keywords = [k.strip() for k in keywords if k.strip()]
        self.interest_model.topic_keywords["monitored"] = list(self.monitored_keywords)
        if self.monitored_keywords:
            self.interest_model.topics["monitored"] = 0.90
        elif "monitored" in self.interest_model.topics:
            self.interest_model.topics.pop("monitored", None)

    def evaluate(
        self,
        stimulus: Stimulus,
        scene_state: Optional[SceneState],
        active_open_loops: list[dict[str, Any]],
        now: Optional[float] = None
    ) -> AttentionResult:
        if now is None:
            now = time.time()

        # --- Layer 1: Hard Attention (Deterministic) ---
        if stimulus.stimulus_type == StimulusType.PROACTIVE_TASK:
            return AttentionResult(
                disposition=AttentionDisposition.WAKE,
                reason="proactive_task_due"
            )

        # Plugin facts (ADR-0018) never wake cognition by themselves — an unclaimed
        # fact has no social reason to speak (Goal 7 / Invariant F & H). Only a
        # condition-bound obligation (TASK_DUE → PROACTIVE_TASK above) may wake.
        if stimulus.stimulus_type == StimulusType.PLUGIN_FACT:
            return AttentionResult(
                disposition=AttentionDisposition.OBSERVE,
                reason="plugin_fact_observed"
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

        # --- Layer 2: Social Continuation (ParticipationThread & Heuristics) ---
        text = stimulus.combined_text
        activity = scene_state.activity_level if scene_state else "quiet"
        last_bot_at = scene_state.recent_bot_message_at if scene_state else None
        time_since_bot = (now - last_bot_at) if last_bot_at else 999999.0

        thread = getattr(scene_state, "current_thread", None) if scene_state else None

        # Check Active Participation Thread continuation (Goal 2 & Goal 3)
        if thread and thread.status.value == "active" and activity != "hot":
            if scene_state.consecutive_bot_messages < 2:
                time_since_rel = now - thread.last_relevant_at
                # Time distance window: [1.0s, 180.0s] since bot, and <= 120s since relevant
                if 1.0 <= time_since_bot <= 180.0 and time_since_rel <= 120.0:
                    is_participant = stimulus.actor_id in thread.participants
                    topic_words = [w for w in thread.topic.split() if len(w) > 1]
                    has_topic_match = any(w in text for w in topic_words) if topic_words else False
                    # Immediate conversational adjacency: 1st message right after bot in active thread
                    is_immediate_adjacency = (time_since_bot <= 60.0 and thread.intervening_messages <= 1)

                    # ADR-0027 (§9.2/9.3): continuation WAKE requires topic_match OR immediate_adjacency.
                    # Participant membership alone is supporting evidence, not independent proof.
                    if has_topic_match or is_immediate_adjacency:
                        if thread.intervening_messages <= 3:
                            return AttentionResult(
                                disposition=AttentionDisposition.WAKE,
                                reason="active_thread_continuation"
                            )
                    elif is_participant:
                        return AttentionResult(
                            disposition=AttentionDisposition.OBSERVE,
                            reason="participant_off_topic"
                        )
                    else:
                        # Speaker is not in thread AND no topic overlap -> Topic drift
                        return AttentionResult(
                            disposition=AttentionDisposition.OBSERVE,
                            reason="topic_drift_or_unrelated_speaker"
                        )

        # Fallback to legacy bot_engagement continuation if thread not initialized yet (§34 & P1)
        elif scene_state and scene_state.bot_engagement == "active" and activity != "hot":
            if last_bot_at and (now - last_bot_at) > 1.0:
                if scene_state.consecutive_bot_messages < 2 and getattr(scene_state, "intervening_messages_since_bot", 0) <= 4:
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
