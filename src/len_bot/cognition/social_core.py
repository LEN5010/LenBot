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
from len_bot.tools.results import ToolResult


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
        jobs: list[dict] | None = None,
        now: float | None = None,
    ) -> list[dict[str, str]]:
        active_open_loop_ids = [item["id"] for item in active_open_loops]

        # ADR-0038: layered persona — identity core block (stable prefix),
        # contract, then per-call adaptive context in the user message.
        system_content = (render_identity_block(self.config) +
            "\n"
            "【群聊理解与表达】\n"
            "你持续观察这个群。结合人物、引用、前文和真实发言理解谁在接谁的话，再选择参与或沉默；没有被@也可能是在追问你。\n"
            "先回应对方实际表达的意思和情绪。可以认同、认真回答、轻微调侃或自然结束，不必每句反问、劝告、制造笑点。被纠正就直接改正。\n"
            "只把 MESSAGE_SENT 当作已说出口，草稿、发送失败和 Shadow 候选不是别人听过的话。无需主动解释自己的机制。\n"
            "收到相处方式的明确反馈时，用有证据的 self_state 或 relationship_updates 调整后续互动，不只口头答应；一次玩笑先放工作认识，不直接归纳成长期习惯。\n"
            "被纠正时先对照原话与已读来源，确有错误就改正；新增约束不等于上一轮出错，不为迎合而否认已经取得的资料或编造过错。\n"
            "相处反馈优先于过去的说话惯性：用后续态度体现变化，不把反馈包装成系统模式切换，也不把验证改变的责任丢回对方。话题结束就让它结束，不用无关旧梗填空。\n"
            "昵称、群名片、偏好称呼和账号ID是不同事实。同名不等于同一人，相邻发言不自动建立称呼归属，角色关系不是群友关系。[图片]是未识别媒体，不是假装已经看懂。\n"
            "当前轮先辨清正在处理的目标和最新约束，再区分原始来源明确支持、来源互相冲突、尚未查明的部分；这些是判断线索，不要求另写状态清单。修订后的约束优先于旧草稿。\n"
            "给出对方此刻能用的结论，必要时附来源和限制。结果不足就说缺什么；只问影响下一步的必要问题，资料已有就继续使用，话题结束可以沉默。\n"
            "【历史、能力与记忆】\n"
            "当前工作状态和最近原话直接可见。只在过去信息会影响理解时用历史、记忆工具；时效事实需要可用的公开查询工具，工具结果回来后再决定表达或沉默。\n"
            "对方提供链接并要求阅读或比较时，先取得对应原文；没有读取就不能说已经看过或查到。背景常识与本次来源明确分开，不把训练印象和额外推测写成来源已经说明的内容。\n"
            "已有工具结果不要重复查询。确实有用的回忆加入人物或关系工作状态，并保留 memory_ids。最近记忆提交回执是已落库的认识，不是保证正确的事实。\n"
            "工具没有给出可核验结果，就保留未确认；空结果既不能证明某事成立，也不能证明不存在；只说明本次查询没有找到，不能宣称所有网上资料都没有。不要用训练印象、传闻或角色代入补成确定答案。需要核实的问题不以玩笑式假定代替结论。尤其人物归属和现实身份，未确认时问清所指，不猜测谁对应谁。\n"
            "记忆修订使用 memory_candidates：upsert 写入；refute 指定一个 target_memory_ids 撤销；supersede 指定旧ID并提供同一人物/类型的新认识，可合并不同key。reason 和 evidence 说明为什么改。没有ID时先 query_memory，不要只在嘴上说划掉。\n"
            "角色资料、表达示例和你自己的猜测不是群聊事实证据；你重复说过也不算新证据。无法确认就保留不确定性，不给群友强加习惯。\n"
            "【执行契约】\n"
            "较长的信息查询可用 job_proposals 创建独立工作：create 提供 proposal_id、goal、constraints_add、source_event_ids，可把本轮 result_ids 移交避免重查。不是每个问题都要创建工作。\n"
            "工作在后台继续时你仍参与对话。只有与该工作有关的补充才 revise；取消用 cancel，中断核对后用 resume；均引用真实 job_id 和 expected_revision。修订不重置预算，结果摘要不是原始证据。\n"
            "是否先接话、告知有用进展或回答追问由你判断，不固定播报步骤。工作相关回复携带 job_id 和 job_revision；只有结果已就绪才能用 fulfils_task_id 确认交付。创建确认仍用 task_ref 引用 proposal_id。\n"
            "你只有提案权，发送、调度、记忆修改都必须由 RuntimeGate 提交。明确行动请求不能只回好；不能把计划、尝试或查询到信息当成已经执行。\n"
            "任务创建提供 proposal_id 和 source_event_ids；确认消息的 task_ref 引用该 proposal_id。修改取消使用真实 task_id。履约消息 fulfils_task_id 引用已有任务；查询任务先 operation=result 保存结果。\n"
            "due_at 是按当前时区换算的 Unix 秒，优先绝对时间，不清楚先问，不把过期承诺重新解释为从现在再等几小时。\n"
            "社会话题 open_threads 和 Runtime OpenLoop 不同。resolve_open_loop_ids 只允许使用 active_open_loops 中的真实ID；reply_to 只用 OneBotMessageID，不用 EventID。\n"
            "当轮决定 silence 不带消息；speak 可带一至三条各自有意义的消息，不机械拆句、不强制短句或无标点。任务确认和履约各自只能关联一条消息。\n"
            "图片需要时通过 inspect_image 实际查看；未配置/失败时不能猜图。发送图片使用 segments 的 image 类型和获准 asset_id，文字用 text 类型；不要自己拼CQ码、路径或URL。表情先查 search_media，适合才用，不强制配图。\n"
            "所有理解更新省略未变化字段；列表用 *_add / *_remove。原始证据引用真实 EventID；别为填满schema重复世界状态。summary 和 reason 是简短内部记录，不要写进群聊回复。\n"
            "最简沉默：{\"perception\":{\"summary\":\"两人在接梗\"},\"decision\":{\"action\":\"silence\",\"reason\":\"没有自然插话位置\"}}\n"
            "最简发言：{\"perception\":{\"summary\":\"A说事情处理好了\"},\"decision\":{\"action\":\"speak\",\"reason\":\"回应A\"},\"message_proposals\":[{\"content\":\"那就好呀\"}]}\n"
            "记忆撤销结构：{\"operation\":\"refute\",\"target_memory_ids\":[\"查询得到的真实记忆ID\"],\"reason\":\"对方指出称呼指向了别人\",\"evidence\":[\"本群纠正消息的真实EventID\"]}；此结构放在 memory_candidates，示例ID不能照抄。\n"
        )

        situation = {
            "current_time": datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo("Asia/Shanghai")).isoformat(),
            "tasks": tasks or [],
            "information_jobs": jobs or [],
            "information_jobs_enabled": self.config.jobs_enabled,
            "group_identity": session.group_identity.model_dump(mode="json"),
            "social_world_state": session.social_world.model_dump(mode="json"),
            "self_social_state": session.self_social_state.model_dump(mode="json"),
            "working_persons": {
                key: {
                    "display_name": value.display_name,
                    "nickname": value.nickname,
                    "card": value.card,
                    "preferred_name": value.preferred_name,
                    "group_role": value.group_role,
                    "recent_context": value.recent_context,
                    "memory_ids": value.memory_ids,
                }
                for key, value in session.working_persons.items()
            },
            "working_relationships": {
                key: {
                    "familiarity": value.familiarity,
                    "patterns": value.patterns,
                    "memory_ids": value.memory_ids,
                }
                for key, value in session.working_relationships.items()
            },
            "retained_attention": [
                item.model_dump(mode="json") for item in session.retained_attention
            ],
            "pending_next_wake": pending_next_wake,
            "recent_episode_summary": session.recent_episode_summary,
            "active_open_loops": active_open_loops,
            "recent_memory_changes": session.recent_memory_changes,
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
        chat_text = json.dumps(recent_chat, ensure_ascii=False)
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
        self.checkpoint = None
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
        jobs: list[dict] | None = None,
        observe: Callable | None = None,
        commit: Callable | None = None,
        now: float | None = None,
        trace_sink: dict | None = None,
    ) -> tuple[SocialCognitionResult, dict[str, Any]]:
        messages = self.context_assembler.assemble(
            session=session,
            burst=burst,
            raw_events=raw_events,
            active_open_loops=active_open_loops,
            pending_next_wake=pending_next_wake,
            voice_examples=voice_examples,
            tasks=tasks, jobs=jobs, now=now,
        )
        working_messages: list[dict[str, Any]] = list(messages)
        tools = list(toolkit.get_tool_definitions()) if toolkit else []
        working_messages[0]["content"] += (
            "【实际可用能力】\n" + ", ".join(tool["function"]["name"] for tool in tools)
            + "\n上面是本轮可调用的工具，不是角色设定。非官方角色 Bot 仍可搜索公开信息；不能保证搜索到实时事实。"
            "只有实际查询失败或能力列表缺少所需工具时，才说明具体限制。"
            f"本轮最多 {max_steps} 个模型步骤、{max_tool_calls} 次工具执行。预算不足时明确未完成，并记录具体未解决事项；不是没有能力，也不是已经完成。")
        if toolkit:
            tools.append({"type": "function", "function": {
                "name": "request_deliberate", "description": "需要更深推理时切换能力，保持当前上下文",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }})
        tier = CognitiveTier.NORMAL
        trace = trace_sink if trace_sink is not None else {}
        trace.update({"mode": "mock" if self.mock_handler else "live", "path": "social", "steps": [], "escalations": [], "latency_ms": 0, "interim_batches": 0})
        tool_calls_used = 0
        contract_repairs = 0
        final_followup = False
        model_steps_used = 0
        active_open_loop_ids = {item["id"] for item in active_open_loops}

        async def charge_model():
            nonlocal model_steps_used
            if model_steps_used >= max_steps:
                raise RuntimeError("Model-step budget exhausted")
            model_steps_used += 1
            trace["model_calls_used"] = model_steps_used

        async def charge_nested_model():
            if max_steps - model_steps_used <= 1:
                raise ValueError("视觉调用额度不足，需保留最终回应步骤")
            await charge_model()

        if toolkit is not None:
            toolkit.before_nested_model = charge_nested_model

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
            remaining_steps = max_steps - model_steps_used
            if remaining_steps <= 0:
                break
            if toolkit and step:
                # Discovery changes subsequent requests without restarting the episode.
                tools = list(toolkit.get_tool_definitions()) + [tool for tool in tools if tool["function"]["name"] == "request_deliberate"]
            if not final_followup and remaining_steps >= 3 and tool_calls_used < max_tool_calls:
                await incorporate()
            self._fit_context(working_messages, tools, trace)
            if self.checkpoint:
                await self.checkpoint("before_model", {"scene_id": session.scene_id, "step": step,
                    "messages": copy.deepcopy(working_messages), "tools": copy.deepcopy(tools)})
            if self.mock_handler:
                await charge_model()
                result = SocialCognitionResult.model_validate(await self.mock_handler(copy.deepcopy(working_messages)))
                if self.checkpoint:
                    await self.checkpoint("after_model", {"scene_id": session.scene_id, "step": step, "result": result.model_dump(mode="json")})
                working_messages.append({"role": "assistant", "content": result.model_dump_json(exclude_none=True)})
                if not final_followup and max_steps - model_steps_used >= 3 and tool_calls_used < max_tool_calls and await incorporate():
                    working_messages[-1]["content"] += "\n最后一次吸收新增消息，观察截点已固定。保留已有工具结果，剩余预算内完成必要查询后决定；之前草稿未发送。"
                    final_followup = True
                    trace["final_followups"] = 1
                    continue
                if commit is None or await commit(result, trace):
                    return result, trace
                raise RuntimeError("Final decision rejected at commit boundary; input remains pending")
            force_final = remaining_steps == 1 or tool_calls_used >= max_tool_calls
            response, resolution, used_fallback, latency = await self._call_model(
                tier=tier,
                messages=working_messages,
                tools=tools,
                tool_choice="none" if force_final and tools else None,
                attempts=trace.setdefault("attempts", []),
                before_attempt=charge_model,
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
            if self.checkpoint:
                await self.checkpoint("after_model", {"scene_id": session.scene_id, "step": step,
                    "content": message.content, "tool_calls": [
                        {"name": call.function.name, "arguments": call.function.arguments}
                        for call in (getattr(message, "tool_calls", None) or [])]})
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
            if getattr(response.choices[0], "finish_reason", None) == "length":
                raise RuntimeError("Model output truncated before a complete decision")

            if getattr(message, "tool_calls", None):
                if force_final:
                    raise RuntimeError("Model called tools during forced final decision")
                if toolkit is None:
                    raise ValueError("Social Core requested a tool without a retrieval toolkit")
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
                prefetched = {}
                if hasattr(toolkit, "execute_many"):
                    eligible = []
                    for call in message.tool_calls[:max(0, max_tool_calls - tool_calls_used)]:
                        try:
                            args = json.loads(call.function.arguments or "{}")
                        except (ValueError, TypeError):
                            continue
                        if isinstance(args, dict) and call.function.name != "request_deliberate":
                            eligible.append((call.id, call.function.name, args))
                    fetched = await toolkit.execute_many([(name, args) for _, name, args in eligible])
                    prefetched = {entry[0]: result for entry, result in zip(eligible, fetched)}
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
                                current = self.registry.resolve(tier)
                                target = self.registry.resolve(CognitiveTier.DELIBERATE)
                                changed = (current.provider_id, current.model) != (target.provider_id, target.model)
                                if changed:
                                    tier = CognitiveTier.DELIBERATE
                                trace["escalations"].append({"step": step, "reason": "model_requested", "actual_model_changed": changed})
                                outcome = "已切换深度模型，继续当前事项。" if changed else "两个档位配置的是同一模型，未改变模型。继续当前事项，不要重复切换。"
                            else:
                                if tool_call.id in prefetched:
                                    outcome = prefetched[tool_call.id]
                                    if isinstance(outcome, Exception):
                                        raise outcome
                                else:
                                    outcome = await toolkit.execute(tool_call.function.name, arguments)
                            tool_result = outcome if isinstance(outcome, str) else str(outcome)
                            structured = ToolResult.normalize(tool_result)
                            if structured.status in {"error", "unsupported"}:
                                error_name = structured.error_code or structured.status
                                error_message = structured.content
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
                if contract_repairs >= 1 or model_steps_used >= max_steps:
                    raise
                contract_repairs += 1
                trace.setdefault("contract_repairs", []).append({"step": step, "error": str(contract_error)})
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
            if not final_followup and max_steps - model_steps_used >= 3 and tool_calls_used < max_tool_calls and await incorporate():
                working_messages[-1]["content"] += "\n最后一次吸收新增消息，观察截点已固定。保留已有工具结果，剩余预算内完成必要查询后决定；之前草稿未发送。"
                final_followup = True
                trace["final_followups"] = 1
                continue
            if commit is None or await commit(result, trace):
                return result, trace
            raise RuntimeError("Final decision rejected at commit boundary; input remains pending")

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
        lines = json.loads(raw)
        removed = 0
        while lines and total > budget:
            line = lines.pop(0)
            total -= estimate_tokens(json.dumps(line, ensure_ascii=False))
            removed += 1
        messages[1]["content"] = head + "【RECENT RAW CONVERSATION】\n" + json.dumps(lines, ensure_ascii=False) + "\n【END RAW CONVERSATION】\n" + tail
        trace["raw_events_rolled_out"] = trace.get("raw_events_rolled_out", 0) + removed
        if estimate_tokens(json.dumps(messages, ensure_ascii=False)) + overhead > budget:
            raise RuntimeError("Working state and tool results exceed context budget; input retained for next turn")

    async def _call_model(
        self,
        tier: CognitiveTier,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None = None,
        attempts: list | None = None,
        before_attempt=None,
    ) -> tuple[Any, Any, bool, float]:
        primary = self.registry.resolve(tier)
        call_started = time.monotonic()
        if before_attempt:
            await before_attempt()
        try:
            started = time.monotonic()
            response = await self._create_completion(primary, messages, tools, tool_choice)
            if attempts is not None:
                attempts.append({"provider": primary.provider_id, "model": primary.model, "success": True,
                                 "latency_ms": round((time.monotonic()-started)*1000)})
            return response, primary, False, time.monotonic() - started
        except Exception as error:
            if attempts is not None:
                attempts.append({"provider": primary.provider_id, "model": primary.model, "success": False,
                                 "error": type(error).__name__, "latency_ms": round((time.monotonic()-started)*1000)})
            if self.metrics:
                self.metrics.record_error(tier.value, primary.provider_id, primary.model, str(error))
            fallback = self.registry.resolve_fallback()
            if (
                fallback is None
                or (fallback.provider_id, fallback.model) == (primary.provider_id, primary.model)
            ):
                raise
            started = time.monotonic()
            if before_attempt:
                await before_attempt()
            try:
                response = await self._create_completion(fallback, messages, tools, tool_choice)
            except Exception as fallback_error:
                if attempts is not None:
                    attempts.append({"provider": fallback.provider_id, "model": fallback.model, "success": False,
                                     "fallback": True, "error": type(fallback_error).__name__,
                                     "latency_ms": round((time.monotonic()-started)*1000)})
                if self.metrics:
                    self.metrics.record_error(
                        tier.value,
                        fallback.provider_id,
                        fallback.model,
                        str(fallback_error),
                    )
                raise
            if attempts is not None:
                attempts.append({"provider": fallback.provider_id, "model": fallback.model, "success": True,
                                 "fallback": True, "latency_ms": round((time.monotonic()-started)*1000)})
            return response, fallback, True, time.monotonic() - call_started

    async def _create_completion(self, resolution, messages, tools, tool_choice=None):
        kwargs: dict[str, Any] = {
            "model": resolution.model,
            "messages": copy.deepcopy(messages),
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "max_tokens": self.context_assembler.config.social_output_reserve_tokens,
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
