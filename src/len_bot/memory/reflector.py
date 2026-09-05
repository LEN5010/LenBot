"""Reflection proposes evidence-backed beliefs and review items, never tasks."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable
from pydantic import Field
from len_bot.cognition.session import SessionModel, SocialWorldPatch, SocialMemoryCandidate
from len_bot.cognition.projection import project_event
from len_bot.events.models import Event
from len_bot.memory.models import EpisodeRecord, MemoryProposal


class ReviewItem(SessionModel):
    summary: str
    source_event_ids: list[str]


class ReflectionOutput(SessionModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    memory_proposals: list[SocialMemoryCandidate] = Field(default_factory=list)
    social_world_patch: SocialWorldPatch | None = None
    review_items: list[ReviewItem] = Field(default_factory=list)


class LLMReflector:
    def __init__(self, resolver: Callable, max_events: int = 30):
        self.resolver = resolver
        self.max_events = max_events

    async def __call__(self, events: list[Event], context: dict | None = None):
        window = events[-self.max_events:]
        if not window:
            raise ValueError("Reflection requires events")
        context = context or {}
        event_ids = [e.id for e in window]
        transcript = "\n".join(project_event(e, context.get("bot_qq", "")) for e in window)
        client, model = self.resolver()
        response = await client.chat.completions.create(
            model=model, temperature=0.2, response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "从原始事件理解群体、人物和关系，形成有证据的记忆和增量社会状态。"
                    "原文引用优于旧摘要；不要将 Bot 自己的猜测当作事实证据。"
                    "只有未安排或冲突的承诺才提出 review_items，绝不能安排任务或承诺执行。"
                    "结合现有任务核对是否已安排；过期事项只记录待核对，不改成从现在开始等待。"
                    "topic id 使用 topic:真实事件ID；关闭话题只能引用当前状态存在的 ID。"
                    "每条记忆和核对事项引用本批真实事件，省略未变化字段，只输出符合 schema 的 JSON。\n"
                    + json.dumps(ReflectionOutput.model_json_schema(), ensure_ascii=False)
                )},
                {"role": "user", "content": json.dumps({
                    "now": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    "context": context, "events": transcript,
                }, ensure_ascii=False)},
            ],
        )
        parsed = ReflectionOutput.model_validate_json(response.choices[0].message.content or "")
        known = set(event_ids)
        for evidence in ([m.evidence for m in parsed.memory_proposals]
                         + [r.source_event_ids for r in parsed.review_items]):
            if not evidence or not set(evidence).issubset(known):
                raise ValueError("Reflection evidence must belong to this batch")
        patch = parsed.social_world_patch
        if patch:
            known_topics = {t["id"] for t in context.get("social_world", {}).get("topics", [])}
            if any(t.id.removeprefix("topic:") not in known and t.id not in known_topics for t in patch.open_topics):
                raise ValueError("Reflection topic lacks evidence")
            if not set(patch.close_topic_ids).issubset(known_topics):
                raise ValueError("Reflection closes unknown topic")
            patch.source_event_ids = event_ids
        episode = EpisodeRecord(
            scene_id=window[0].scene_id, title=parsed.title, summary=parsed.summary,
            source_event_ids=event_ids, participants=sorted({e.actor_id for e in window}),
            tags=parsed.tags, created_at=time.time(),
        )
        memories = [MemoryProposal(**m.model_dump()) for m in parsed.memory_proposals]
        return episode, memories, patch, parsed.review_items
