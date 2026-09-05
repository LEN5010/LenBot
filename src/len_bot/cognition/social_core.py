from __future__ import annotations

import json
import copy
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from collections.abc import Awaitable, Callable
from typing import Any

from len_bot.cognition.persona import (
    render_identity_block,
    render_register_block,
    render_voice_examples_block,
)
from len_bot.cognition.providers import ProviderRegistry
from len_bot.cognition.projection import (
    estimate_tokens,
    pack_recent_chat,
    project_event,
    project_onebot_text,
)
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
        voice_examples: list[dict] | None = None,
        tasks: list[dict] | None = None,
        now: float | None = None,
    ) -> list[dict[str, str]]:
        active_open_loop_ids = [item["id"] for item in active_open_loops]

        # ADR-0038: layered persona — identity core block (stable prefix),
        # contract, then per-call adaptive context in the user message.
        system_content = (
            render_identity_block(self.config)
            + "\n【SOCIAL COGNITION CONTRACT】\n"
            "你是持续存在于群聊中的同一个社会成员。先理解新事件如何改变当前社会场景，再决定是否说话。\n"
            "SILENCE 是正常且重要的结果：看懂但没有自然插话位置时保持沉默。\n"
            "不要依赖关键词决定话题延续；结合人物、前文、群体互动和你刚才的行为判断。\n"
            "不要把事实查询结果写成客服报告；最终表达必须保持同一个群友人格。\n"
            "你只能输出结构化认知和 proposal，无权执行发送、调度、记忆写入或任何副作用。\n"
            "近期上下文不足且过去经历会影响理解时，主动使用历史与记忆工具。\n"
            "不要无条件查询；只有确实需要回忆人物、关系、旧话题或具体经历时才调用。\n"
            "当前消息明确在@你或回复你，且只是寒暄、简短指令或能直接回答的问题时，直接完成判断，不要为了凑上下文查询历史。\n"
            "SocialWorldState.open_threads 是你理解出的社会话题；resolve_open_loop_ids 是 Runtime 事务字段，两者不是同一种 ID。\n"
            "resolve_open_loop_ids 只能填写 CURRENT SOCIAL STATE 的 active_open_loops 中真实存在的 id；没有匹配项时必须输出空数组。\n"
            "message_proposals.reply_to 只能填写 OneBotMessageID，绝不能填写 EventID；不需要引用回复时填 null。\n"
            "保持输出紧凑，只保留当前真正活跃的话题、人物更新和必要证据，避免重复复述整个聊天记录。\n"
            "工具结果回到你这里判断是否还有必要说，别人已经回答时可以沉默。"
            "明确的提醒、查询、改时间、取消和追问进度必须处理，不能只回好。"
            "创建任务给 proposal_id，确认消息 task_ref 引用它；修改取消填写真实 task_id。"
            "履约消息 fulfils_task_id 指向已有任务；查询结果先用 operation=result 保存。"
            "due_at 使用当前时区换算的 Unix 秒，优先绝对时间，不明确时先追问。"
            "不要将已过时承诺重新按相对时间安排。source_event_ids 引用真实请求证据。"
            "没有被@不等于无需回应：识别别人是否在接你的话、质疑你或隐式邀请。"
            "不知道的时效事实主动查证，查询失败就承认未知；[图片]表示尚未识别的媒体。"
            "需要更深处理时调用 request_deliberate，这只切换同一人格的能力。"
        )

        situation = {
            "current_time": datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo("Asia/Shanghai")).isoformat(),
            "tasks": tasks or [],
            "group_identity": session.group_identity.model_dump(mode="json"),
            "social_world_state": session.social_world.model_dump(mode="json"),
            "self_social_state": session.self_social_state.model_dump(mode="json"),
            "working_persons": {
                key: {
                    "display_name": value.display_name,
                    "group_role": value.group_role,
                    "recent_context": value.recent_context[-10:],
                }
                for key, value in session.working_persons.items()
            },
            "working_relationships": {
                key: {
                    "familiarity": value.familiarity,
                    "patterns": value.patterns[-10:],
                }
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
        projected_burst = self._project_onebot_text(burst.combined_text)
        situation_json = json.dumps(situation, ensure_ascii=False)
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        fixed_user_content = (
            "【CURRENT SOCIAL STATE】\n"
            f"{situation_json}\n\n"
            "【CURRENT BURST】\n"
            f"{projected_burst}\n"
            f"SourceEventIDs: {burst.source_event_ids}\n"
            f"MentionBot: {burst.has_mention_bot} | ReplyBot: {burst.has_reply_bot}\n\n"
            "【RUNTIME REFERENCE AUTHORITY】\n"
            f"允许关闭的 Runtime OpenLoop IDs: {json.dumps(active_open_loop_ids, ensure_ascii=False)}\n\n"
            "输出一个 JSON 对象，必须严格符合以下 schema；perception.world_patch 只输出发生变化的字段，未变化的省略。\n"
            f"{schema_json}"
        )
        input_budget = max(
            0,
            self.config.social_context_window_tokens
            - self.config.social_output_reserve_tokens
            - self._estimate_tokens(system_content)
            - self._estimate_tokens(fixed_user_content)
            - estimate_tokens(render_register_block(session))
            - estimate_tokens(render_voice_examples_block(voice_examples or [])),
        )
        recent_chat = self._pack_recent_chat(raw_events, input_budget)
        chat_text = "\n".join(recent_chat) if recent_chat else "(暂无近期原始对话)"
        register_block = render_register_block(session)
        examples_block = render_voice_examples_block(voice_examples or [])
        user_content = (
            "【CURRENT SOCIAL STATE】\n"
            f"{situation_json}\n\n"
            + register_block
            + "【RECENT RAW CONVERSATION】\n"
            f"{chat_text}\n\n"
            + "【END RAW CONVERSATION】\n"
            + examples_block
            + "【CURRENT BURST】\n"
            f"{projected_burst}\n"
            f"SourceEventIDs: {burst.source_event_ids}\n"
            f"MentionBot: {burst.has_mention_bot} | ReplyBot: {burst.has_reply_bot}\n\n"
            "【RUNTIME REFERENCE AUTHORITY】\n"
            f"允许关闭的 Runtime OpenLoop IDs: {json.dumps(active_open_loop_ids, ensure_ascii=False)}\n\n"
            "输出一个 JSON 对象，必须严格符合以下 schema；perception.world_patch 只输出发生变化的字段，未变化的省略。\n"
            f"{schema_json}"
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _pack_recent_chat(self, raw_events: list[Event], token_budget: int) -> list[str]:
        return pack_recent_chat(raw_events, token_budget, self.config.bot_qq)

    def _project_event(self, event: Event) -> str:
        return project_event(event, self.config.bot_qq)

    @staticmethod
    def _project_onebot_text(text: str) -> str:
        return project_onebot_text(text)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return estimate_tokens(text)


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
        voice_examples: list[dict] | None = None,
        tasks: list[dict] | None = None,
        observe: Callable | None = None,
        commit: Callable | None = None,
        now: float | None = None,
    ) -> tuple[SocialCognitionResult, dict[str, Any]]:
        messages = self.context_assembler.assemble(
            session=session,
            burst=burst,
            raw_events=raw_events,
            active_open_loops=active_open_loops,
            pending_next_wake=pending_next_wake,
            voice_examples=voice_examples,
            tasks=tasks, now=now,
        )
        working_messages: list[dict[str, Any]] = list(messages)
        tools = list(toolkit.get_tool_definitions()) if toolkit else []
        if toolkit:
            tools.append({"type": "function", "function": {
                "name": "request_deliberate", "description": "需要更深推理时切换能力，保持当前上下文",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }})
        tier = CognitiveTier.NORMAL
        trace: dict[str, Any] = {"mode": "mock" if self.mock_handler else "live", "path": "social", "steps": [], "escalations": [], "latency_ms": 0, "interim_batches": 0}
        tool_calls_used = 0
        contract_repairs = 0
        active_open_loop_ids = {item["id"] for item in active_open_loops}

        async def incorporate() -> bool:
            if observe is None:
                return False
            new_context = await observe()
            if new_context is None:
                return False
            working_messages.append({"role": "user", "content": new_context})
            trace["interim_batches"] += 1
            return True

        for step in range(max_steps):
            await incorporate()
            self._fit_context(working_messages, tools, trace)
            if self.mock_handler:
                result = SocialCognitionResult.model_validate(await self.mock_handler(copy.deepcopy(working_messages)))
                if await incorporate():
                    continue
                if commit is None or await commit(result, trace):
                    return result, trace
                continue
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
                if tier == CognitiveTier.DELIBERATE:
                    self.metrics.inc_social("cognition_deliberate_calls")
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
            trace["latency_ms"] += step_trace["latency_ms"]
            step_trace["estimated_prompt_tokens"] = estimate_tokens(json.dumps(working_messages, ensure_ascii=False)) + estimate_tokens(json.dumps(tools, ensure_ascii=False))
            step_trace["token_estimate_error"] = step_trace["prompt_tokens"] - step_trace["estimated_prompt_tokens"]

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
                            if tool_calls_used >= max_tool_calls:
                                raise ValueError("本回合工具预算已耗尽，请直接给出决定")
                            if tool_call.function.name == "request_deliberate":
                                tier = CognitiveTier.DELIBERATE
                                trace["escalations"].append({"step": step, "reason": "model_requested"})
                                outcome = "已切换深度能力，继续当前事项。"
                            else:
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
                    tool_calls_used += 1
                continue

            content = message.content or ""
            try:
                result = self._parse_result(content)
                invalid_loop_ids = set(result.resolve_open_loop_ids) - active_open_loop_ids
                if invalid_loop_ids:
                    raise ValueError(
                        "resolve_open_loop_ids contains non-Runtime IDs: "
                        + ", ".join(sorted(invalid_loop_ids))
                    )
            except Exception as contract_error:
                if contract_repairs >= 1 or step >= max_steps - 1:
                    raise
                contract_repairs += 1
                working_messages.append({"role": "assistant", "content": content})
                working_messages.append({
                    "role": "user",
                    "content": (
                        "上一份结果不能提交："
                        f"{contract_error}。请重新输出完整、紧凑且符合 schema 的 JSON。"
                        "不要调用工具。resolve_open_loop_ids 只能使用允许列表中的真实 Runtime ID；"
                        "SocialWorldState.open_threads 的自定义 ID 不能放进去。"
                    ),
                })
                continue

            working_messages.append({"role": "assistant", "content": content})
            if await incorporate():
                continue
            if commit is None or await commit(result, trace):
                return result, trace

        raise RuntimeError("Cognition budget exhausted before a fresh decision could commit")

    def _fit_context(self, messages, tools, trace):
        """Only evict the oldest raw edge. Active state and completed tools stay."""
        config = self.context_assembler.config
        budget = config.social_context_window_tokens - config.social_output_reserve_tokens
        overhead = estimate_tokens(json.dumps(tools, ensure_ascii=False))
        total = estimate_tokens(json.dumps(messages, ensure_ascii=False)) + overhead
        if total <= budget:
            return
        head, raw = messages[1]["content"].split("【RECENT RAW CONVERSATION】\n", 1)
        raw, tail = raw.split("【END RAW CONVERSATION】\n", 1)
        lines = raw.splitlines(keepends=True)
        removed = 0
        while lines and total > budget:
            line = lines.pop(0)
            total -= estimate_tokens(json.dumps(line, ensure_ascii=False))
            removed += 1
        messages[1]["content"] = head + "【RECENT RAW CONVERSATION】\n" + "".join(lines) + "【END RAW CONVERSATION】\n" + tail
        trace["raw_lines_rolled_out"] = trace.get("raw_lines_rolled_out", 0) + removed
        if estimate_tokens(json.dumps(messages, ensure_ascii=False)) + overhead > budget:
            raise RuntimeError("Working state and tool results exceed context budget; input retained for next turn")

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
            "messages": copy.deepcopy(messages),
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
