"""Anti-slop style guard (ADR-0038 §7).

A purely local detector over the bot's recent messages per scene: exact
duplicates, repeated openers, and repeated character n-grams. It feeds metrics
and, on a strong anomaly, at most one corrective FAST retry — never a per-reply
LLM critic and never a content rejection on its own.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Sequence

_OPENER_CHARS = 4
_NGRAM_CHARS = 6
_HISTORY_WINDOW = 12


@dataclass
class StyleSignal:
    duplicate: bool = False
    repeated_opener: bool = False
    repeated_ngram: bool = False

    @property
    def strong_anomaly(self) -> bool:
        return self.duplicate or self.repeated_opener

    def describe(self) -> str:
        parts = []
        if self.duplicate:
            parts.append("与近期发言完全重复")
        if self.repeated_opener:
            parts.append("开头与近期多条发言雷同")
        if self.repeated_ngram:
            parts.append("与近期发言有大段重复")
        return ";".join(parts) or "ok"


def analyze(content: str, recent: Sequence[str]) -> StyleSignal:
    text = content.strip()
    if not text:
        return StyleSignal()
    signal = StyleSignal()
    if any(text == prior.strip() for prior in recent):
        signal.duplicate = True

    opener = text[:_OPENER_CHARS]
    if opener and sum(1 for prior in recent if prior.strip().startswith(opener)) >= 2:
        signal.repeated_opener = True

    if len(text) >= _NGRAM_CHARS:
        hits: dict[str, int] = {}
        for prior in recent:
            for i in range(len(prior.strip()) - _NGRAM_CHARS + 1):
                gram = prior.strip()[i:i + _NGRAM_CHARS]
                if gram in text:
                    hits[gram] = hits.get(gram, 0) + 1
        if any(count >= 2 for count in hits.values()):
            signal.repeated_ngram = True
    return signal


class StyleGuard:
    """Per-scene rolling history of the bot's own visible messages."""

    def __init__(self):
        self._recent: dict[str, Deque[str]] = {}

    def observe(self, scene_id: str, content: str) -> None:
        bucket = self._recent.setdefault(scene_id, deque(maxlen=_HISTORY_WINDOW))
        bucket.append(content.strip())

    def evaluate(self, scene_id: str, content: str) -> StyleSignal:
        bucket = self._recent.get(scene_id)
        if not bucket:
            return StyleSignal()
        return analyze(content, list(bucket))

    def forget_scene(self, scene_id: str) -> None:
        self._recent.pop(scene_id, None)
