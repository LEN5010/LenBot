"""Process-local social counters and latency windows.

Actual model requests, usage and failure accounting live in model_calls. This
cache does not count model tokens or maintain a second routing ledger.
"""

from collections import deque
from typing import Deque, Optional


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, round(pct / 100.0 * (len(sorted_values) - 1))))
    return round(sorted_values[idx], 3)


class RuntimeMetrics:
    LATENCY_WINDOW = 200

    def __init__(self):
        self._latencies: dict[str, Deque[float]] = {}
        self.social: dict[str, int] = {
            "human_messages": 0,
            "social_cognition": 0,
            "intentional_silence": 0,
            "social_would_speak": 0,
            "gate_action": 0,
            "visible_messages": 0,
            "unsolicited_visible_messages": 0,
            "would_send": 0,
            "cancellations_honored": 0,
            "stale_outcomes_rejected": 0,
            "followups_incorporated": 0,
            "openloops_resolved": 0,
            "tasks_started": 0,
            "tasks_completed": 0,
            "retrieval_tool_calls": 0,
            "retrieval_tool_errors": 0,
            "retrieval_forced_finals": 0,
            "bursts_total": 0,
            "cognition_attempts": 0,
            "cognition_committed": 0,
            "cognition_failed": 0,
            "gate_rejected": 0,
        }

    def record_latency(self, phase: str, seconds: float) -> None:
        """ADR-0038 §8: production pipeline phase latency (event→burst, burst→request,
        model_total, parse, gate, queue→send, end_to_end)."""
        bucket = self._latencies.get(phase)
        if bucket is None:
            bucket = deque(maxlen=self.LATENCY_WINDOW)
            self._latencies[phase] = bucket
        bucket.append(max(0.0, seconds))

    def inc_social(self, key: str, amount: int = 1) -> None:
        self.social[key] = self.social.get(key, 0) + amount

    def snapshot(self) -> dict:
        visible = self.social.get("visible_messages", 0)
        human = self.social.get("human_messages", 0)
        latency = {}
        for phase, values in self._latencies.items():
            ordered = sorted(values)
            latency[phase] = {
                "p50_s": _percentile(ordered, 50),
                "p95_s": _percentile(ordered, 95),
                "samples": len(ordered),
            }
        return {
            "latency": latency,
            "social": {
                **self.social,
                "visible_speech_ratio": round(visible / human, 4) if human else 0.0,
            },
        }
