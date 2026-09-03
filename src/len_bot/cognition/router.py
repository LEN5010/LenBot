import logging
from enum import StrEnum
from typing import Optional

logger = logging.getLogger(__name__)

class CognitiveTier(StrEnum):
    NORMAL = "normal"
    DELIBERATE = "deliberate"

class CognitionRouter:
    """Dynamic Normal→Deliberate escalation during an Episode (§70-76, ADR-0020).

    Model selection moved to the ProviderRegistry; the router only decides WHEN
    to escalate and returns the reason (recorded as a routing metric).
    """

    COMPLEXITY_MARKER = "[COMPLEXITY: HIGH]"
    LARGE_TOOL_RESULT_CHARS = 1200
    MULTI_STEP_TRIGGER = 3

    def should_escalate(
        self,
        current_tier: CognitiveTier,
        step_count: int,
        latest_tool_result: Optional[str] = None
    ) -> Optional[str]:
        """Returns the escalation reason, or None when the tier should stay."""
        if current_tier == CognitiveTier.DELIBERATE:
            return None  # Already at highest tier

        # 1. Complexity trigger: large unstructured payload or explicit complexity marker (§75)
        if latest_tool_result:
            if len(latest_tool_result) > self.LARGE_TOOL_RESULT_CHARS:
                reason = f"tool_payload_length:{len(latest_tool_result)}chars"
                logger.info("CognitionRouter: Escalating to DELIBERATE due to tool payload length (%d chars)", len(latest_tool_result))
                return reason
            if self.COMPLEXITY_MARKER in latest_tool_result or "争议" in latest_tool_result:
                reason = "tool_complexity_marker"
                logger.info("CognitionRouter: Escalating to DELIBERATE due to complexity flag in tool output")
                return reason

        # 2. Multi-step reasoning trigger: investigation taking multiple steps
        if step_count >= self.MULTI_STEP_TRIGGER:
            reason = f"multi_step_trajectory:{step_count}"
            logger.info("CognitionRouter: Escalating to DELIBERATE due to deep multi-step ReAct trajectory (step %d)", step_count)
            return reason

        return None
