from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from len_bot.cognition.providers import ProviderRegistry
from len_bot.cognition.router import CognitiveTier
from len_bot.cognition.session import GroupAgentSession, SocialCognitionResult
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, Stimulus


class SocialCoreContextAssembler:
    def __init__(self, config: RuntimeConfig):
        self.config = config

    def assemble(
        self,
        session: GroupAgentSession,
        burst: Stimulus,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        pending_next_wake: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        recent_chat = []
        for event in raw_events[-80:]:
            if event.raw_text:
                actor = "你(Bot)" if event.actor_id == f"user:{self.config.bot_qq}" else event.actor_id
                recent_chat.append(f"[{event.id}] {actor}: {event.raw_text}")

        system_content = (
            "【CORE SELF】\n"
            f"你的名字是：{self.config.identity_name}\n"
            f"{self.config.identity_persona}\n\n"
            "【SOCIAL COGNITION CONTRACT】\n"
            "你是持续存在于群聊中的同一个社会成员。先理解新事件如何改变当前社会场景，再决定是否说话。\n"
            "SILENCE 是正常且重要的结果：看懂但没有自然插话位置时保持沉默。\n"
            "不要依赖关键词决定话题延续；结合人物、前文、群体互动和你刚才的行为判断。\n"
            "不要把事实查询结果写成客服报告；最终表达必须保持同一个群友人格。\n"
            "你只能输出结构化认知和 proposal，无权执行发送、调度、记忆写入或任何副作用。\n"
            "当前阶段不执行工具；需要外部事实但无法判断时，保持沉默并在 reason 中说明。"
        )

        situation = {
            "group_identity": session.group_identity.model_dump(mode="json"),
            "social_world_state": session.social_world.model_dump(mode="json"),
            "self_social_state": session.self_social_state.model_dump(mode="json"),
            "working_persons": {
                key: value.model_dump(mode="json")
                for key, value in session.working_persons.items()
            },
            "working_relationships": {
                key: value.model_dump(mode="json")
                for key, value in session.working_relationships.items()
            },
            "retained_attention": [
                item.model_dump(mode="json") for item in session.retained_attention
            ],
            "pending_next_wake": pending_next_wake,
            "recent_episode_summary": session.recent_episode_summary,
            "active_open_loops": active_open_loops,
        }
        schema = SocialCognitionResult.model_json_schema()
        chat_text = "\n".join(recent_chat) if recent_chat else "(暂无近期原始对话)"
        user_content = (
            "【CURRENT SOCIAL STATE】\n"
            f"{json.dumps(situation, ensure_ascii=False)}\n\n"
            "【RECENT RAW CONVERSATION】\n"
            f"{chat_text}\n\n"
            "【CURRENT BURST】\n"
            f"{burst.combined_text}\n"
            f"SourceEventIDs: {burst.source_event_ids}\n"
            f"MentionBot: {burst.has_mention_bot} | ReplyBot: {burst.has_reply_bot}\n\n"
            "输出一个 JSON 对象，必须严格符合以下 schema；social world 输出理解后的完整当前快照。\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]


class SocialCognitionCore:
    def __init__(
        self,
        config: RuntimeConfig,
        registry: ProviderRegistry,
        metrics: Any = None,
        mock_handler: Callable[[list[dict[str, str]]], Awaitable[SocialCognitionResult]] | None = None,
    ):
        self.registry = registry
        self.metrics = metrics
        self.mock_handler = mock_handler
        self.context_assembler = SocialCoreContextAssembler(config)

    async def execute(
        self,
        session: GroupAgentSession,
        burst: Stimulus,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        pending_next_wake: dict[str, Any] | None = None,
    ) -> tuple[SocialCognitionResult, dict[str, Any]]:
        messages = self.context_assembler.assemble(
            session=session,
            burst=burst,
            raw_events=raw_events,
            active_open_loops=active_open_loops,
            pending_next_wake=pending_next_wake,
        )
        if self.mock_handler:
            result = await self.mock_handler(messages)
            return SocialCognitionResult.model_validate(result), {"mode": "mock", "steps": []}

        resolution = self.registry.resolve(CognitiveTier.NORMAL)
        started = time.monotonic()
        try:
            response = await resolution.client.chat.completions.create(
                model=resolution.model,
                messages=messages,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
        except Exception as error:
            if self.metrics:
                self.metrics.record_error(
                    CognitiveTier.NORMAL.value,
                    resolution.provider_id,
                    resolution.model,
                    str(error),
                )
            raise

        latency = time.monotonic() - started
        usage = getattr(response, "usage", None)
        if self.metrics:
            self.metrics.record_call(
                CognitiveTier.NORMAL.value,
                resolution.provider_id,
                resolution.model,
                latency,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )

        content = response.choices[0].message.content or ""
        result = self._parse_result(content)
        trace = {
            "mode": "live",
            "steps": [
                {
                    "step": 0,
                    "tier": CognitiveTier.NORMAL.value,
                    "provider_id": resolution.provider_id,
                    "model": resolution.model,
                    "tool_calls": [],
                }
            ],
        }
        return result, trace

    @staticmethod
    def _parse_result(text: str) -> SocialCognitionResult:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace < 0 or last_brace <= first_brace:
            raise ValueError("Social Core returned no JSON object")
        return SocialCognitionResult.model_validate_json(text[first_brace:last_brace + 1])
