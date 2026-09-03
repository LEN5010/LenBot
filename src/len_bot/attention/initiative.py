import time
from enum import StrEnum
from typing import Optional, Tuple
from len_bot.scenes.models import SceneState
from len_bot.state.interest import InterestModel
from len_bot.attention.budget import SpeakingBudget

class InitiativeDisposition(StrEnum):
    DISCARD = "DISCARD"
    RETAIN_FOR_LATER = "RETAIN_FOR_LATER"
    WAKE_FOR_INITIATIVE = "WAKE_FOR_INITIATIVE"

class InitiativeEngine:
    """Calculates proactive participation opportunities based on interest and budget (§83-86)."""

    def __init__(self, interest_model: InterestModel, speaking_budget: SpeakingBudget):
        self.interest_model = interest_model
        self.speaking_budget = speaking_budget

    def evaluate(
        self,
        text: str,
        scene_state: Optional[SceneState],
        now: Optional[float] = None
    ) -> Tuple[InitiativeDisposition, str, float]:
        if now is None:
            now = time.time()

        interest_score, matched_topics = self.interest_model.score_text(text)
        threshold = self.speaking_budget.calculate_threshold(scene_state, now)

        if interest_score >= threshold and interest_score > 0.6:
            reason = f"Initiative triggered: interest {interest_score:.2f} >= budget threshold {threshold:.2f} (topics: {matched_topics})"
            return InitiativeDisposition.WAKE_FOR_INITIATIVE, reason, interest_score
        elif interest_score >= 0.4:
            reason = f"Initiative retained (cooldown/budget): interest {interest_score:.2f} below threshold {threshold:.2f}"
            return InitiativeDisposition.RETAIN_FOR_LATER, reason, interest_score
        else:
            return InitiativeDisposition.DISCARD, "Low interest", interest_score
