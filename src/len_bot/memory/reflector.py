"""LLM Reflector (ADR-0019 §10.5): turns an unreflected event range into an
L1 EpisodeRecord plus evidence-backed L2 MemoryProposals.

The reflector is a cognition-tier consumer: it only PROPOSES memories; the
MemoryGate / atomic commit chain remains the sole write authority (Invariant E).
Evidence integrity is enforced downstream — every proposal here cites the full
reflected event range, all of which provably exist in the scene scope.
"""

import json
import logging
import re
import time
from typing import Any, Callable

from openai import AsyncOpenAI

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
    "禁止给人贴人格标签（如\"内向\"），禁止无证据推断敏感属性。\n"
    "只输出一个 JSON 对象，不要输出其他文本：\n"
    '{"title": "...", "summary": "...", "tags": ["..."], '
    '"memory_proposals": [{"subject": "user:123", "kind": "preference", "key": "...", '
    '"value": "...", "certainty": "tentative|likely|strong|explicit", '
    '"human_readable_assertion": "..."}]}'
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

    async def __call__(self, events: list[Event]) -> tuple[EpisodeRecord, list[MemoryProposal]]:
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

        return episode, proposals
