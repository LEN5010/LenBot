from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from len_bot.cognition.providers import ProviderRegistry
from len_bot.cognition.router import CognitionRouter, CognitiveTier
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
            f"{self.config.identity_persona}\n"
            f"你的说话风格：{self.config.conversation_style}\n\n"
            "【SOCIAL COGNITION CONTRACT】\n"
            "你是持续存在于群聊中的同一个社会成员。先理解新事件如何改变当前社会场景，再决定是否说话。\n"
            "SILENCE 是正常且重要的结果：看懂但没有自然插话位置时保持沉默。\n"
            "不要依赖关键词决定话题延续；结合人物、前文、群体互动和你刚才的行为判断。\n"
            "不要把事实查询结果写成客服报告；最终表达必须保持同一个群友人格。\n"
            "你只能输出结构化认知和 proposal，无权执行发送、调度、记忆写入或任何副作用。\n"
            "近期上下文不足且过去经历会影响理解时，主动使用历史与记忆工具。\n"
            "不要无条件查询；只有确实需要回忆人物、关系、旧话题或具体经历时才调用。\n"
            "当前消息明确在@你或回复你，且只是寒暄、简短指令或能直接回答的问题时，直接完成判断，不要为了凑上下文查询历史。\n"
            "工具结果只是你的回忆材料，最终仍由你以同一个人格输出完整结构化认知。"
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
        router: CognitionRouter | None = None,
    ):
        self.registry = registry
        self.metrics = metrics
        self.mock_handler = mock_handler
        self.router = router or CognitionRouter()
        self.context_assembler = SocialCoreContextAssembler(config)

    async def execute(
        self,
        session: GroupAgentSession,
        burst: Stimulus,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        pending_next_wake: dict[str, Any] | None = None,
        toolkit: Any | None = None,
        max_steps: int = 5,
        max_tool_calls: int = 6,
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
            return SocialCognitionResult.model_validate(result), {"mode": "mock", "steps": [], "escalations": []}

        working_messages: list[dict[str, Any]] = list(messages)
        tools = toolkit.get_tool_definitions() if toolkit else None
        tier = CognitiveTier.NORMAL
        trace: dict[str, Any] = {"mode": "live", "steps": [], "escalations": []}
        tool_calls_used = 0

        for step in range(max_steps):
            force_final = step == max_steps - 1 or tool_calls_used >= max_tool_calls
            response, resolution, used_fallback, latency = await self._call_model(
                tier=tier,
                messages=working_messages,
                tools=tools,
                tool_choice="none" if force_final and tools else None,
            )
            usage = getattr(response, "usage", None)
            if self.metrics:
                self.metrics.record_call(
                    tier.value,
                    resolution.provider_id,
                    resolution.model,
                    latency,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                )
                if used_fallback:
                    self.metrics.inc_social("model_fallbacks")
                if force_final:
                    self.metrics.inc_social("retrieval_forced_finals")

            message = response.choices[0].message
            step_trace: dict[str, Any] = {
                "step": step,
                "tier": tier.value,
                "provider_id": resolution.provider_id,
                "model": resolution.model,
                "fallback": used_fallback,
                "forced_final": force_final,
                "latency_ms": round(latency * 1000),
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "tool_calls": [],
            }
            trace["steps"].append(step_trace)

            if getattr(message, "tool_calls", None):
                if toolkit is None:
                    raise ValueError("Social Core requested a tool without a retrieval toolkit")
                if force_final:
                    raise RuntimeError("Social Core ignored forced final decision")
                # Minimal assistant echo: echoing the full SDK message carries
                # provider-specific null fields (refusal/function_call/annotations)
                # that some OpenAI-compatible endpoints reject with 400 — exactly
                # when a fallback vendor is in play.
                working_messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments or "{}",
                            },
                        }
                        for tool_call in message.tool_calls
                    ],
                })
                for tool_call in message.tool_calls:
                    arguments: Any = {}
                    error_name, error_message = "", ""
                    try:
                        arguments = json.loads(tool_call.function.arguments or "{}")
                        if not isinstance(arguments, dict):
                            error_name = "invalid_arguments"
                            error_message = "tool arguments must decode to a JSON object"
                    except (json.JSONDecodeError, TypeError) as decode_error:
                        error_name = "invalid_arguments"
                        error_message = str(decode_error)

                    tool_result: str | None = None
                    if not error_name:
                        try:
                            outcome = await toolkit.execute(tool_call.function.name, arguments)
                            tool_result = outcome if isinstance(outcome, str) else str(outcome)
                        except Exception as tool_failure:
                            error_name = type(tool_failure).__name__
                            error_message = str(tool_failure)

                    if error_name:
                        error_payload = json.dumps(
                            {"error": error_name, "message": error_message},
                            ensure_ascii=False,
                        )
                        working_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": error_payload,
                        })
                        step_trace["tool_calls"].append({
                            "name": tool_call.function.name,
                            "arguments": arguments,
                            "error": error_message,
                            "result_preview": error_payload[:300],
                        })
                        if self.metrics:
                            self.metrics.inc_social("retrieval_tool_errors")
                    else:
                        working_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        })
                        step_trace["tool_calls"].append({
                            "name": tool_call.function.name,
                            "arguments": arguments,
                            "result_preview": tool_result[:300],
                        })
                        if self.metrics:
                            self.metrics.inc_social("retrieval_tool_calls")
                        reason = self.router.should_escalate(tier, step + 1, tool_result)
                        if reason:
                            tier = CognitiveTier.DELIBERATE
                            trace["escalations"].append({"step": step, "reason": reason})
                            if self.metrics:
                                self.metrics.record_escalation(reason)
                    tool_calls_used += 1
                continue

            return self._parse_result(message.content or ""), trace

        raise RuntimeError("Social Core loop exited without decision")

    async def _call_model(
        self,
        tier: CognitiveTier,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None = None,
    ) -> tuple[Any, Any, bool, float]:
        primary = self.registry.resolve(tier)
        try:
            started = time.monotonic()
            response = await self._create_completion(primary, messages, tools, tool_choice)
            return response, primary, False, time.monotonic() - started
        except Exception as error:
            if self.metrics:
                self.metrics.record_error(tier.value, primary.provider_id, primary.model, str(error))
            fallback = self.registry.resolve_fallback()
            if (
                fallback is None
                or (fallback.provider_id, fallback.model) == (primary.provider_id, primary.model)
            ):
                raise
            started = time.monotonic()
            try:
                response = await self._create_completion(fallback, messages, tools, tool_choice)
            except Exception as fallback_error:
                if self.metrics:
                    self.metrics.record_error(
                        tier.value,
                        fallback.provider_id,
                        fallback.model,
                        str(fallback_error),
                    )
                raise
            return response, fallback, True, time.monotonic() - started

    @staticmethod
    async def _create_completion(resolution, messages, tools, tool_choice=None):
        kwargs: dict[str, Any] = {
            "model": resolution.model,
            "messages": messages,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        return await resolution.client.chat.completions.create(**kwargs)

    @staticmethod
    def _parse_result(text: str) -> SocialCognitionResult:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace < 0 or last_brace <= first_brace:
            raise ValueError("Social Core returned no JSON object")
        return SocialCognitionResult.model_validate_json(text[first_brace:last_brace + 1])
