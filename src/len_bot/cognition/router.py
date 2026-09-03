import logging
from enum import StrEnum
from typing import Optional
from len_bot.config import RuntimeConfig

logger = logging.getLogger(__name__)

class CognitiveTier(StrEnum):
    NORMAL = "normal"
    DELIBERATE = "deliberate"

class CognitionRouter:
    """Manages cognitive model tier and dynamic escalation during an Episode (§70-76)."""

    def __init__(self, config: RuntimeConfig):
        self.config = config

    def get_model_for_tier(self, tier: CognitiveTier) -> str:
        if tier == CognitiveTier.DELIBERATE:
            return self.config.deliberate_model
        return self.config.default_model

    def should_escalate(
        self,
        current_tier: CognitiveTier,
        step_count: int,
        latest_tool_result: Optional[str] = None
    ) -> bool:
        if current_tier == CognitiveTier.DELIBERATE:
            return False  # Already at highest tier

        # 1. Complexity trigger: large unstructured payload or explicit complexity marker (§75)
        if latest_tool_result:
            if len(latest_tool_result) > 1200:
                logger.info("CognitionRouter: Escalating to DELIBERATE due to tool payload length (%d chars)", len(latest_tool_result))
                return True
            if "[COMPLEXITY: HIGH]" in latest_tool_result or "争议" in latest_tool_result:
                logger.info("CognitionRouter: Escalating to DELIBERATE due to complexity flag in tool output")
                return True

        # 2. Multi-step reasoning trigger: investigation taking multiple steps
        if step_count >= 3:
            logger.info("CognitionRouter: Escalating to DELIBERATE due to deep multi-step ReAct trajectory (step %d)", step_count)
            return True

        return False
