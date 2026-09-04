"""Runtime Metrics (ADR-0020): routing + social behavior counters, in-memory.

Routing metrics record every live LLM call per (tier, provider, model): calls,
errors, tokens, latency. Social counters distinguish cognition, intentional
silence, proposed speech, gate approval, and physical/shadow delivery.
In-memory only — they reset on restart; durable state remains event-owned.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class RouteStats:
    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=200))


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, round(pct / 100.0 * (len(sorted_values) - 1))))
    return round(sorted_values[idx], 3)


class RuntimeMetrics:
    MAX_ESCALATIONS = 100
    LATENCY_WINDOW = 200

    def __init__(self):
        self._routes: dict[tuple[str, str, str], RouteStats] = {}
        self._provider_errors: dict[str, int] = {}
        self._escalations: Deque[tuple[float, str]] = deque(maxlen=self.MAX_ESCALATIONS)
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
            "obligations_fulfilled": 0,
            "model_fallbacks": 0,
            "retrieval_tool_calls": 0,
            "retrieval_tool_errors": 0,
            "retrieval_forced_finals": 0,
            # ADR-0038: FAST/FULL routing + style metrics
            "bursts_total": 0,
            "cognition_fast_calls": 0,
            "cognition_fast_speak": 0,
            "cognition_fast_silence": 0,
            "cognition_fast_to_full": 0,
            "cognition_full_calls": 0,
            "cognition_deliberate_calls": 0,
            "style_slop_flags": 0,
            "style_retries": 0,
        }

    def record_latency(self, phase: str, seconds: float) -> None:
        """ADR-0038 §8: production pipeline phase latency (event→burst, burst→request,
        model_total, parse, gate, queue→send, end_to_end)."""
        bucket = self._latencies.get(phase)
        if bucket is None:
            bucket = deque(maxlen=self.LATENCY_WINDOW)
            self._latencies[phase] = bucket
        bucket.append(max(0.0, seconds))

    def _route(self, tier: str, provider_id: str, model: str) -> RouteStats:
        key = (tier, provider_id, model)
        if key not in self._routes:
            self._routes[key] = RouteStats()
        return self._routes[key]

    def record_call(
        self,
        tier: str,
        provider_id: str,
        model: str,
        latency_seconds: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        stats = self._route(tier, provider_id, model)
        stats.calls += 1
        stats.latencies.append(max(0.0, latency_seconds))
        stats.prompt_tokens += prompt_tokens
        stats.completion_tokens += completion_tokens

    def record_error(self, tier: str, provider_id: str, model: str, error: str) -> None:
        stats = self._route(tier, provider_id, model)
        stats.errors += 1
        self._provider_errors[provider_id] = self._provider_errors.get(provider_id, 0) + 1

    def record_escalation(self, reason: str) -> None:
        self._escalations.append((time.time(), reason))

    def inc_social(self, key: str, amount: int = 1) -> None:
        self.social[key] = self.social.get(key, 0) + amount

    def snapshot(self) -> dict:
        routes = []
        for (tier, provider_id, model), stats in self._routes.items():
            ordered = sorted(stats.latencies)
            routes.append({
                "tier": tier,
                "provider_id": provider_id,
                "model": model,
                "calls": stats.calls,
                "errors": stats.errors,
                "prompt_tokens": stats.prompt_tokens,
                "completion_tokens": stats.completion_tokens,
                "latency_p50_s": _percentile(ordered, 50),
                "latency_p95_s": _percentile(ordered, 95),
            })
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
            "routes": routes,
            "provider_errors": dict(self._provider_errors),
            "escalation_total": len(self._escalations),
            "recent_escalation_reasons": [reason for _, reason in list(self._escalations)[-10:]],
            "latency": latency,
            "social": {
                **self.social,
                "visible_speech_ratio": round(visible / human, 4) if human else 0.0,
                "fast_full_ratio": round(
                    self.social.get("cognition_fast_calls", 0) / self.social.get("cognition_full_calls", 0), 4
                ) if self.social.get("cognition_full_calls", 0) else None,
            },
        }
