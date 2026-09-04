"""LLM Reflector (ADR-0019 §10.5, ADR-0038): turns an unreflected event range
into an L1 EpisodeRecord, evidence-backed L2 MemoryProposals, and a deferred
merge-only SocialWorldPatch.

The reflector is a cognition-tier consumer: it only PROPOSES; the MemoryGate /
atomic commit chain remains the sole write authority for memories, and the
SocialWorldPatch merges into the session only through the SceneActor's lawful
event path. Evidence integrity is enforced downstream.
"""

import json
import logging
import re
import time
from typing import Any, Callable

from openai import AsyncOpenAI

from len_bot.cognition.session import SocialWorldPatch
from len_bot.events.models import Event
from len_bot.memory.models import EpisodeRecord, MemoryProposal, MemoryCertainty

logger = logging.getLogger(__name__)

_REFLECT_SYSTEM_PROMPT = (
    "你是一个社交记忆反思器。给你一段群聊/私聊的原始事件记录（带事件ID），"
    "请产出：\n"
    "1. 一个 EpisodeRecord：这次社交互动的简短压缩记录。\n"
    "2. 若干 MemoryProposal：只有当记录中有明确证据支撑时才提出，必须是 typed 的社会记忆，\n"
    "   kind 只能是：preference / habit / relationship / fact / group_norm / topic_interest / recurring_role / social_pattern。\n"
    "   subject 用 user:QQ号 / group:群号 / bot-in-group:群号。例如：\n"
    "   - 某人经常组织活动 → recurring_role\n"
    "   - 某人对某话题兴趣高 → topic_interest\n"
    "   - 群里晚上活跃 → group_norm\n"
    "   - Bot 在该群常参与某类话题 → social_pattern\n"
    "3. 一个 social_world_patch（可省略）：对当前社会世界的延迟理解，只做增量合并——\n"
    "   mood/activity 一句话；open_topics 只开真正活跃的新话题，id 必须是 topic:<某个真实事件ID>；\n"
    "   close_topic_ids 只填确认已经结束的旧话题 id；social_dynamics_add 是新的群体互动模式观察；\n"
    "   group_identity 只在确有证据时更新。\n"
    "禁止给人贴人格标签（如\"内向\"），禁止无证据推断敏感属性。\n"
    "只输出一个 JSON 对象，不要输出其他文本：\n"
    '{"title": "...", "summary": "...", "tags": ["..."], '
    '"memory_proposals": [{"subject": "user:123", "kind": "preference", "key": "...", '
    '"value": "...", "certainty": "tentative|likely|strong|explicit", '
    '"human_readable_assertion": "..."}], '
    '"social_world_patch": {"mood": "...", "open_topics": [{"id": "topic:<事件ID>", '
    '"subject": "...", "context": "..."}], "close_topic_ids": [], "social_dynamics_add": []}}'
)


class LLMReflector:
    def __init__(self, resolver: Callable[[], tuple[AsyncOpenAI, str]], max_events: int = 30):
        # ADR-0020: route resolved lazily per reflection so provider hot-swaps apply.
        self.resolver = resolver
        self.max_events = max_events

    def _extract_json(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"Reflector output has no JSON object: {text[:120]}")
        return json.loads(text[start:end + 1])

    async def __call__(self, events: list[Event]) -> tuple[EpisodeRecord, list[MemoryProposal], SocialWorldPatch | None]:
        window = events[-self.max_events:]
        event_ids = [e.id for e in window]
        participants = sorted({e.actor_id for e in window if e.actor_id})
        transcript = "\n".join(
            f"[{e.id}] ({time.strftime('%m-%d %H:%M', time.localtime(e.timestamp))}) {e.actor_id}: {e.raw_text}"
            for e in window
            if e.raw_text
        ) or "(无文本内容)"

        client, model = self.resolver()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _REFLECT_SYSTEM_PROMPT},
                {"role": "user", "content": f"场景事件记录：\n{transcript}"},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        parsed = self._extract_json(raw)

        episode = EpisodeRecord(
            scene_id=window[0].scene_id if window else "",
            title=str(parsed.get("title") or "对话记录")[:100],
            summary=str(parsed.get("summary") or "")[:500],
            source_event_ids=event_ids,
            participants=participants,
            tags=[str(t) for t in parsed.get("tags", [])][:10],
            created_at=time.time(),
        )

        proposals: list[MemoryProposal] = []
        for mp in parsed.get("memory_proposals", []):
            try:
                proposals.append(MemoryProposal(
                    subject=str(mp["subject"]),
                    kind=str(mp["kind"]),
                    key=str(mp["key"]),
                    value=str(mp["value"]),
                    certainty=MemoryCertainty(mp.get("certainty", "tentative")),
                    evidence=list(event_ids),
                    human_readable_assertion=str(mp.get("human_readable_assertion") or mp["value"]),
                ))
            except Exception as e:
                logger.warning("Skipping malformed memory proposal from reflector: %s (%s)", mp, e)

        patch = self._parse_patch(parsed.get("social_world_patch"), event_ids)
        return episode, proposals, patch

    def _parse_patch(self, data: Any, event_ids: list[str]) -> SocialWorldPatch | None:
        """Merge-only deferred patch; invalid shapes are dropped, never fatal."""
        if not isinstance(data, dict) or not data:
            return None
        try:
            patch = SocialWorldPatch.model_validate(data)
        except Exception as e:
            logger.warning("Dropping malformed social_world_patch from reflector: %s", e)
            return None
        known = set(event_ids)
        valid_topics = []
        for topic in patch.open_topics:
            grounding = topic.id.removeprefix("topic:")
            if grounding in known:
                valid_topics.append(topic)
            else:
                logger.warning("Dropping ungrounded reflector topic %s", topic.id)
        patch.open_topics = valid_topics
        patch.close_topic_ids = [
            tid for tid in patch.close_topic_ids if tid.removeprefix("topic:") in known
        ]
        patch.source_event_ids = event_ids
        if not any([
            patch.mood,
            patch.activity,
            patch.open_topics,
            patch.close_topic_ids,
            patch.social_dynamics_add,
            patch.group_identity is not None,
        ]):
            return None
        return patch
