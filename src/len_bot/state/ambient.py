"""Ambient Retained Items — short-lived soft state (ADR-0018, Goal 7).

An AmbientItem records something the agent recently saw that *might* become
useful later. It is deliberately NOT durable (unlike OpenLoop/Task/Memory):
items live for hours, never precipitate into long-term state on their own
(Invariant F), and only enter cognition as 【RELEVANT AMBIENT ITEMS】 when a
wake episode's topic lexically matches. Long-term retention requires an
explicit MemoryProposal through Reflection.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from len_bot.state.interest import InterestModel


DEFAULT_AMBIENT_TTL_SECONDS = 21600.0  # 6h — "hours → days", never permanent
MATCH_THRESHOLD = 0.30
MAX_MATCHED_ITEMS = 3


@dataclass
class AmbientItem:
    id: str
    scope: str
    source: str
    topic: str
    summary: str
    salience: float
    retained_at: float
    expires_at: float
    evidence_event_id: str = ""


class AmbientStore:
    def __init__(
        self,
        interest_model: Optional[InterestModel] = None,
        default_ttl_seconds: float = DEFAULT_AMBIENT_TTL_SECONDS,
        match_threshold: float = MATCH_THRESHOLD,
        max_matched: int = MAX_MATCHED_ITEMS,
    ):
        self._items: dict[str, AmbientItem] = {}
        self._interest_model = interest_model or InterestModel()
        self.default_ttl_seconds = default_ttl_seconds
        self.match_threshold = match_threshold
        self.max_matched = max_matched

    def retain(
        self,
        scope: str,
        source: str,
        topic: str,
        summary: str,
        salience: float,
        evidence_event_id: str = "",
        now: Optional[float] = None,
        retained_at: Optional[float] = None,
    ) -> AmbientItem:
        now = now if now is not None else time.time()
        if retained_at is not None:
            now = retained_at
        salience = max(0.0, min(1.0, salience))
        item = AmbientItem(
            id=f"amb_{uuid.uuid4().hex[:10]}",
            scope=scope,
            source=source,
            topic=topic,
            summary=summary,
            salience=salience,
            retained_at=now,
            expires_at=now + self.default_ttl_seconds,
            evidence_event_id=evidence_event_id,
        )
        self._items[item.id] = item
        return item

    @staticmethod
    def _topic_keywords(topic: str) -> list[str]:
        return [w for w in topic.replace("，", " ").replace(",", " ").split() if len(w) > 1]

    def score_match(self, item: AmbientItem, text: str) -> float:
        """Layer-1 lexical heuristic (ADR-0014 §7.3 analog): word overlap × salience."""
        keywords = self._topic_keywords(item.topic)
        if not keywords:
            # Fallback to the global InterestModel when the item has no own keywords
            return self._interest_model.score_text(text)[0] * item.salience
        hits = sum(1 for w in keywords if w in text)
        return (hits / len(keywords)) * item.salience

    def match(self, text: str, scope: str, now: Optional[float] = None) -> list[AmbientItem]:
        """Return high-relevance live items for the given scene scope, best first."""
        now = now if now is not None else time.time()
        scored: list[tuple[float, AmbientItem]] = []
        for item in self._items.values():
            if item.expires_at <= now:
                continue
            # Scope guard: scene-local items stay in their scene; global-safe travel anywhere.
            if item.scope not in (scope, "global-safe"):
                continue
            score = self.score_match(item, text)
            if score >= self.match_threshold:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: self.max_matched]]

    def sweep(self, now: Optional[float] = None) -> list[str]:
        now = now if now is not None else time.time()
        expired = [item_id for item_id, item in self._items.items() if item.expires_at <= now]
        for item_id in expired:
            del self._items[item_id]
        return expired

    def items(self) -> list[AmbientItem]:
        return list(self._items.values())
