import time
from typing import Optional
from len_bot.scenes.models import SceneState

class SpeakingBudget:
    """Manages scene speaking budget and dynamic initiative threshold (§82)."""

    def __init__(self, base_threshold: float = 0.60):
        self.base_threshold = base_threshold

    def calculate_threshold(self, scene_state: Optional[SceneState], now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()

        if not scene_state:
            return self.base_threshold

        threshold = self.base_threshold

        # 1. Monologue prevention: bot spoke consecutively
        if scene_state.consecutive_bot_messages == 1:
            threshold += 0.20
        elif scene_state.consecutive_bot_messages >= 2:
            threshold += 0.50  # Strongly block unsolicited consecutive bot messages

        # 2. Recency penalty: bot spoke in the last 2 minutes
        if scene_state.recent_bot_message_at:
            delta = now - scene_state.recent_bot_message_at
            if delta < 60.0:
                threshold += 0.35
            elif delta < 180.0:
                threshold += 0.15

        # 3. Scene density penalty: hot fast-scrolling group
        if scene_state.activity_level == "hot":
            threshold += 0.20

        return min(1.0, threshold)
