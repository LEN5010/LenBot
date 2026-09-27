"""Persistent, scene-serial chat; platform text sending is explicitly injected."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from len_bot.media.images import image_block
from .config import LabConfig
from .context import (
    CompactionPlan, ContextBudgetError, estimate_content, estimate_request, estimate_text_request,
    plan_compaction, project_history,
)
from .discovery import DEFERRED_NAMES, TOOL_SEARCH, ToolSearchArguments, search_tools
from .delivery import part_length, report_parts, split_expression
from .images import LOOK_TOOL, LookArguments, execute_look
from .messages import ChatMessage, Segment, Sender, SendResult, render_message
from .model import ChatModel, ModelProtocolError, ModelReply, ToolCall
from .model_slots import ModelSlots
from .memory import MEMORY_TOOL, MemoryService
from .persona import Persona, select_examples, select_style
from .persona_knowledge import PERSONA_KNOWLEDGE_TOOL, PersonaKnowledgeArguments, persona_knowledge
from .pricing import estimate_cost
from .recall import RECALL_TOOL, RecallArguments, recall_chat
from .schedule import SCHEDULE_TOOLS, describe, execute_schedule
from .store import ImageAsset, Store, encode
from .tasks import WorkTasks
from .tasks_tools import DELEGATE_TOOL, TASK_TOOL, execute_tasks
from .web_read import WEB_READ_TOOL, WebReadArguments, execute_web_read
from .web_search import WEB_SEARCH_TOOL, WebSearchArguments, execute_web_search


class SayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1)
    reply_to: str | None = None
    mention: str | None = Field(default=None, pattern=r"^[0-9]+$")
    length: Literal["短", "正常", "长"] = "正常"


class WaitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seconds: float = Field(ge=0, allow_inf_nan=False)
    reason: str

    @field_validator("reason")
    @classmethod
    def required_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value


SAY_TOOL = {"type": "function", "function": {
    "name": "say", "description": "在当前场景表达；隔离环境仅模拟发送。",
    "parameters": SayArguments.model_json_schema(),
}}
WAIT_TOOL = {"type": "function", "function": {
    "name": "wait", "description": "短时等待补充消息，新消息可提前结束；受本轮剩余时限约束。",
    "parameters": WaitArguments.model_json_schema(),
}}
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def tool_catalog(*, platform: bool) -> list[dict]:
    say = SAY_TOOL if not platform else {"type": "function", "function": {
        **SAY_TOOL["function"], "description": "在当前场景表达；结果返回实际原文和平台发送状态。",
    }}
    return [say, WAIT_TOOL, RECALL_TOOL, WEB_SEARCH_TOOL, WEB_READ_TOOL, LOOK_TOOL,
            *SCHEDULE_TOOLS, PERSONA_KNOWLEDGE_TOOL, MEMORY_TOOL, DELEGATE_TOOL, TASK_TOOL, TOOL_SEARCH]


def tool_unavailable_reasons(config: LabConfig, persona: Persona, name: str) -> list[str]:
    reasons = []
    if persona.tools != "all" and name not in persona.tools:
        reasons.append("当前角色未允许此工具")
    if name == "web_read" and config.web_read is None:
        reasons.append("尚未配置网页读取")
    if name == "web_search" and config.web_search is None:
        reasons.append("尚未配置搜索服务")
    if name == "memory" and config.memory is None:
        reasons.append("尚未配置长期记忆后端")
    if name == "look" and config.models.roles.vision is None:
        reasons.append("尚未配置视觉模型")
    if name == "schedule" and not config.schedules.enabled:
        reasons.append("当前场景未开启一次性安排")
    if name in {"delegate", "task"} and config.worker is None:
        reasons.append("尚未配置任务容器与 worker 模型")
    if name == "delegate" and not config.tasks.enabled:
        reasons.append("当前场景未开放委托任务")
    if name == "persona_knowledge" and not persona.knowledge:
        reasons.append("角色包没有 knowledge/ 资料")
    return reasons


def build_tools(config: LabConfig, persona: Persona, *, platform: bool) -> list[dict]:
    if persona.tools != "all":
        for name in ("web_read", "web_search", "look", "persona_knowledge", "memory"):
            if name in persona.tools:
                reasons = tool_unavailable_reasons(config, persona, name)
                if reasons:
                    raise ValueError(f"角色开放 {name} 但无法装配：{'；'.join(reasons)}")
    allowed = [tool for tool in tool_catalog(platform=platform)
               if not tool_unavailable_reasons(config, persona, tool["function"]["name"])]
    names = {tool["function"]["name"] for tool in allowed}
    if "schedule" in names and not {"schedule_list", "schedule_cancel"} <= names:
        raise ValueError("角色开放 schedule 时必须同时开放 schedule_list 和 schedule_cancel")
    if "delegate" in names and "task" not in names:
        raise ValueError("角色开放 delegate 时必须同时开放 task 管理工具")
    if names & DEFERRED_NAMES and "tool_search" not in names:
        raise ValueError("角色开放低频工具时必须同时开放 tool_search")
    return allowed


def voice_prompt(persona: Persona) -> str:
    return Template((PROMPTS / "next_voice.md").read_text()).substitute(
        name=persona.name, brief=persona.brief,
        self_reference="、".join(persona.self_reference), voice=persona.voice,
        boundaries=persona.boundaries,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)),
    )


def build_system(config: LabConfig, persona: Persona, allowed: list[dict], *, platform: bool) -> str:
    """Render the actual stable mind system text for this scene and outlet."""
    names = {tool["function"]["name"] for tool in allowed}
    deferred = [tool for tool in allowed if tool["function"]["name"] in DEFERRED_NAMES]
    mode = "next_direct.md" if config.voice_mode == "direct" else "next_intent.md"
    expression_mode = Template((PROMPTS / mode).read_text()).substitute(
        voice=persona.voice,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)))
    system = Template((PROMPTS / "next_mind.md").read_text()).substitute(
        name=persona.name, scene=config.scene, bot_qq=config.bot_qq,
        brief=persona.brief, self_reference="、".join(persona.self_reference),
        aliases="、".join(persona.aliases), behavior=persona.behavior,
        boundaries=persona.boundaries, expression_mode=expression_mode,
        outlet=(PROMPTS / ("next_platform_outlet.md" if platform else
                           "next_simulated_outlet.md")).read_text().strip(),
    )
    scene_details = {}
    if config.persona_aliases:
        scene_details["本场景对你的称呼"] = config.persona_aliases
    if config.relationships:
        scene_details["关系说明（QQ → 描述）"] = dict(sorted(config.relationships.items()))
    if config.behavior_addendum is not None:
        scene_details["本场景行为补充"] = config.behavior_addendum
    if scene_details:
        system += "\n" + Template((PROMPTS / "next_scene_persona.md").read_text()).substitute(
            details=encode(scene_details),
        )
    if "schedule" in names:
        system += "\n" + (PROMPTS / "next_schedule.md").read_text()
    if "memory" in names:
        system += "\n" + Template((PROMPTS / "next_memory.md").read_text()).substitute(
            backend=config.memory.backend,
            backend_details=(PROMPTS / f"next_memory_{config.memory.backend}.md").read_text(),
        )
    elif config.memory is not None and config.memory.auto_recall:
        system += "\n" + (PROMPTS / "next_memory_recall.md").read_text()
    if "task" in names:
        system += "\n" + (PROMPTS / "next_tasks.md").read_text()
    if "tool_search" in names:
        system += "\n" + Template((PROMPTS / "next_tools.md").read_text()).substitute(
            catalog="\n".join(f"- {tool['function']['name']}：{tool['function']['description'].split('；')[0]}"
                              for tool in deferred) or "（当前没有允许发现的低频工具）")
    return system


class Chat:
    def __init__(self, config: LabConfig, persona: Persona, store: Store,
                 mind: ChatModel, voice: ChatModel, *,
                 vision: ChatModel | None = None,
                 memory: MemoryService | None = None,
                 tasks: WorkTasks | None = None,
                 slots: ModelSlots | None = None,
                 send_text: Callable[[ChatMessage], Awaitable[SendResult]] | None = None,
                 on_update: Callable[[], None] | None = None,
                 on_compaction: Callable[[], None] | None = None,
                 now: Callable[[], float] = time.time):
        self.config, self.persona, self.store = config, persona, store
        self.now = now
        self.mind, self.voice, self.vision = mind, voice, vision
        if (config.memory is None) != (memory is None):
            raise ValueError("memory 服务必须与根配置的记忆后端一起提供")
        self.memory = memory
        if (config.worker is None) != (tasks is None):
            raise ValueError("任务服务必须与根配置的 worker 一起提供")
        self.tasks = tasks
        self.slots = slots
        self.direct_request = False
        self.send_text = send_text
        self.on_update = on_update
        self.on_compaction = on_compaction
        previous = self.store.last_mind_request(config.scene)
        if previous is not None:
            current = mind.settings.model_dump(exclude={"api_key"})
            if any(previous["settings"][key] != current[key] for key in ("api", "base_url", "model")):
                _, active = self.store.active_history(config.scene)
                if any(message["role"] in {"assistant", "tool"} for _, message in active):
                    raise ValueError("大脑模型绑定已改变；当前会话仍含旧提供方原生条目，"
                                     "请停机执行显式可移植历史转换")
        if (config.models.roles.vision is None) != (vision is None):
            raise ValueError("vision 客户端必须与根配置的视觉模型绑定一起提供")
        allowed = build_tools(config, persona, platform=send_text is not None)
        self.allowed_tool_names = {tool["function"]["name"] for tool in allowed}
        self.core_tools = [tool for tool in allowed if tool["function"]["name"] not in DEFERRED_NAMES]
        self.deferred_tools = [tool for tool in allowed if tool["function"]["name"] in DEFERRED_NAMES]
        saved = self.store.load_discovered_tools(config.scene)
        self.discovered_tools = set(saved) & self.allowed_tool_names & DEFERRED_NAMES
        self.system = build_system(config, persona, allowed, platform=send_text is not None)

    @property
    def tools(self) -> list[dict]:
        return self.core_tools + [tool for tool in self.deferred_tools
                                  if tool["function"]["name"] in self.discovered_tools]

    def notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    @property
    def tool_names(self) -> set[str]:
        return {tool["function"]["name"] for tool in self.tools}

    def restore(self) -> bool:
        previous = self.store.last_mind_request(self.config.scene)
        resume = self.store.recover(self.config.scene)
        if set(self.store.load_discovered_tools(self.config.scene)) != self.discovered_tools:
            self.store.save_discovered_tools(self.config.scene, sorted(self.discovered_tools))
        if previous is not None and previous["messages"][0]["content"] != self.system:
            self.store.append(self.config.scene, {"role": "user", "content":
                "本次启动已更新角色或表达模式；当前系统设定生效，已有聊天原文保留。"})
        history = self.store.recent(self.config.scene, 1)
        if history:
            last = datetime.fromtimestamp(history[-1].time, ZoneInfo(self.config.timezone)).isoformat()
            self.store.append(self.config.scene, {"role": "user", "content":
                f"隔离会话已恢复。上次保存聊天时间：{last}。离线期间的消息尚未取得。"})
        return resume

    def render(self, message: ChatMessage) -> str:
        quote = (None if message.reply_to is None else
                 self.store.find_message(message.scene, message.reply_to))
        return render_message(message, timezone=self.config.timezone, reply=quote)

    async def request(self, turn_id: str, role: Literal["mind", "voice", "recap", "vision"],
                      messages: list[dict], tools: list[dict], *,
                      recap_target: CompactionPlan | None = None) -> ModelReply:
        model_role = "mind" if role == "recap" else role
        model = getattr(self, model_role)
        binding = getattr(self.config.models.roles, model_role)
        output_tokens = self.config.compaction.max_output_tokens if role == "recap" else binding.max_output_tokens
        estimate = estimate_text_request if role == "vision" else estimate_request
        estimated = estimate(messages, tools, output_tokens)
        if estimated > binding.context_window_tokens:
            scope = "文本部分" if role == "vision" else "请求"
            raise ContextBudgetError(
                f"{role} {scope}含预留输出估算 {estimated} token，超过配置窗口 {binding.context_window_tokens}；未调用模型")
        settings = model.settings.model_dump(exclude={"api_key"})
        settings["max_output_tokens"] = output_tokens
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        async with (self.slots.slot(direct=self.direct_request) if self.slots is not None else nullcontext()):
            call_id = self.store.start_call(turn_id, role, {
                "settings": settings, "messages": messages, "tools": tools,
                "provider": binding.provider,
                "price": None if price is None else price.model_dump(mode="json"),
                **({"estimated_text_tokens": estimated, "estimated_total_tokens": None} if role == "vision"
                   else {"estimated_total_tokens": estimated}),
                "context_window_tokens": binding.context_window_tokens,
            })
            self.notify()
            reply = None
            try:
                if role == "recap":
                    reply = await model.complete(messages, tools, max_output_tokens=output_tokens)
                else:
                    reply = await model.complete(messages, tools)
                if role == "vision" and (reply.tool_calls or not reply.text.strip()):
                    raise ValueError(f"视觉模型未返回完整描述：{encode(reply.message)}")
                if recap_target is not None:
                    if reply.tool_calls or not reply.text.strip():
                        raise ValueError(f"压缩模型未返回完整回想：{encode(reply.message)}")
                    content_tokens = estimate_content(reply.text)
                    if content_tokens > recap_target.summary_budget_tokens:
                        raise ContextBudgetError(
                            f"新回想正文估算 {content_tokens} token，超过本步回想预算 "
                            f"{recap_target.summary_budget_tokens}；未切换起点")
            except BaseException as error:
                response = None if reply is None else {"message": reply.message, "finish_reason": reply.finish_reason}
                usage = None if reply is None else reply.usage
                token_usage = None if reply is None else reply.token_usage
                if isinstance(error, ModelProtocolError):
                    response, usage = error.response, error.usage
                    token_usage = error.token_usage
                self.store.end_call(call_id, response, usage, f"{type(error).__name__}: {error}",
                                    cost=estimate_cost(price, token_usage))
                self.notify()
                raise
            self.store.end_call(call_id, {"message": reply.message, "finish_reason": reply.finish_reason}, reply.usage,
                                cost=estimate_cost(price, reply.token_usage),
                                append_to_scene=self.config.scene if role == "mind" else None,
                                recap_for=None if recap_target is None else (self.config.scene, recap_target.through))
            self.notify()
            return reply

    async def describe_image(self, turn_id: str, asset: ImageAsset) -> str:
        messages = [
            {"role": "system", "content": (PROMPTS / "next_vision.md").read_text()},
            {"role": "user", "content": [
                {"type": "text", "text": encode({"width": asset.width, "height": asset.height,
                                                "animated_first_frame_only": asset.animated})},
                image_block(asset.jpeg),
            ]},
        ]
        return (await self.request(turn_id, "vision", messages, [])).text

    def project(self, recap: str | None, entries: list[tuple[int, dict]], state: dict) -> list[dict]:
        return [{"role": "system", "content": self.system}] + project_history(recap, entries) + [state]

    async def prepare_context(self, turn_id: str, *, expression_style: str | None = None,
                              recalled: str | None = None) -> list[dict]:
        binding = self.config.models.roles.mind
        trigger = int(binding.context_window_tokens * self.config.compaction.trigger_ratio)
        while True:
            recap, entries = self.store.active_history(self.config.scene)
            # Refresh only between model requests, never midway through a tool group.
            self.discovered_tools = set(self.store.load_discovered_tools(self.config.scene))
            now = datetime.fromtimestamp(self.now(), ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
            state = {"role": "user", "content": f"当前时间：{now}"}
            schedules = self.store.list_schedules(self.config.scene, limit=21)
            if schedules:
                state["content"] += "\n<未完成安排>\n" + "\n\n".join(
                    describe(item, preview=True) for item in schedules[:20]) + "\n</未完成安排>"
                if len(schedules) > 20:
                    state["content"] += "\n这里只列前 20 条；schedule_list 可继续查看。"
            if self.config.voice_mode == "direct" and expression_style is not None:
                state["content"] += "\n" + expression_style
            if recalled is not None:
                state["content"] += "\n<相关长期记忆>\n" + recalled + "\n</相关长期记忆>"
            messages = self.project(recap, entries, state)
            if estimate_request(messages, self.tools, binding.max_output_tokens) <= trigger:
                return messages
            plan = plan_compaction(
                entries, system=messages[0], state=state, tools=self.core_tools,
                output_tokens=binding.max_output_tokens, trigger_tokens=trigger,
                keep_recent_entries=self.config.compaction.keep_recent_entries,
                summary_output_tokens=self.config.compaction.max_output_tokens,
                recap=recap, summary_template=(PROMPTS / "next_recap.md").read_text(),
                window_tokens=binding.context_window_tokens,
            )
            await self.request(turn_id, "recap", plan.request_messages, [], recap_target=plan)
            if self.on_compaction is not None:
                self.on_compaction()

    async def express(self, turn_id: str, arguments: SayArguments, *,
                      expression_style: str | None = None) -> ChatMessage:
        quote = None
        if arguments.reply_to is not None:
            quote = self.store.find_message(self.config.scene, arguments.reply_to)
            if quote is None:
                raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if self.config.voice_mode == "direct":
            text = arguments.content
        else:
            messages = [{"role": "system", "content": voice_prompt(self.persona)}]
            for message in self.store.recent_context_messages(self.config.scene):
                if message.is_self and message.send_status in {"received", "sent", "simulated"}:
                    messages.append({"role": "assistant", "content": "".join(
                        segment.data["text"] for segment in message.segments if segment.type == "text")})
                else:
                    rendered = self.render(message)
                    if messages[-1]["role"] == "user":
                        messages[-1]["content"] += "\n" + rendered
                    else:
                        messages.append({"role": "user", "content": rendered})
            task = {"要表达": arguments.content, "长度": arguments.length,
                    "回复对象": None if quote is None else self.render(quote), "提及QQ": arguments.mention}
            if expression_style is not None:
                messages.append({"role": "user", "content": expression_style})
            messages.append({"role": "user", "content": encode(task)})
            reply = await self.request(turn_id, "voice", messages, [])
            if reply.tool_calls or not reply.text.strip():
                raise ValueError(f"表达器未返回完整台词：{encode(reply.message)}")
            text = reply.text
        segments = []
        if arguments.reply_to is not None:
            segments.append(Segment("reply", {"id": arguments.reply_to}))
        if arguments.mention is not None:
            segments.append(Segment("at", {"qq": arguments.mention}))
        segments.append(Segment("text", {"text": text}))
        return self.simulated_message(segments, reply_to=arguments.reply_to)

    async def deliver_expression(self, call_id: str, expression: ChatMessage, *,
                                 turn_id: str) -> tuple[str, str]:
        parts = split_expression(expression, self.config.text_delivery.max_chars)
        entry_seq = self.store.prepare_expression(
            self.config.scene, call_id, report_parts(parts, [], self.render),
        )
        prefix = "模拟表达（未发送到 QQ）：" if self.send_text is None else ""
        return await self.send_prepared_expression(entry_seq, parts, prefix=prefix, turn_id=turn_id)

    async def send_prepared_expression(self, entry_seq: int, parts: list[ChatMessage],
                                       *, prefix: str = "", turn_id: str | None = None) -> tuple[str, str]:
        errors: list[str | None] = []
        settings = self.config.text_delivery
        for index, part in enumerate(parts):
            if index:
                delay = min(settings.max_interval_seconds,
                            max(settings.min_interval_seconds, part_length(part) / settings.chars_per_second))
                await asyncio.sleep(delay)
            part.time = self.now()
            part.send_status = "simulated" if self.send_text is None else "unconfirmed"
            errors.append(None)
            content = report_parts(parts, errors, self.render)
            message_seq = self.store.start_expression_part(entry_seq, part, prefix + content,
                                                          turn_id=turn_id)
            self.notify()
            if self.send_text is not None:
                result = await self.send_text(part)
                part.send_status, part.platform_message_id = result.status, result.platform_message_id
                errors[-1] = result.error
                content = report_parts(parts, errors, self.render)
                self.store.finish_expression((message_seq, entry_seq), part, prefix + content,
                                             turn_id=turn_id)
                self.notify()
                if result.status != "sent":
                    break
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return content, status

    def simulated_message(self, segments: list[Segment], *, reply_to: str | None = None) -> ChatMessage:
        return ChatMessage(
            id=str(uuid4()), platform="qq", scene=self.config.scene, platform_message_id=None,
            sender=Sender(self.config.bot_qq, self.persona.name, None, None), time=self.now(),
            segments=segments, reply_to=reply_to, mentions_bot=False,
            is_self=True, send_status="simulated",
        )

    async def execute_tool(self, turn_id: str, call: ToolCall,
                           wait_for_messages: Callable[[float], Awaitable[str]], *,
                           expression_style: str | None = None,
                           ) -> tuple[str, ChatMessage | None, list[str] | None]:
        if call.name not in self.tool_names:
            raise ValueError(f"当前请求未开放工具：{call.name}")
        if call.name == "tool_search":
            arguments = ToolSearchArguments.model_validate(call.arguments)
            matched = search_tools(arguments.query, self.deferred_tools)
            names = [tool["function"]["name"] for tool in matched]
            discovered = sorted(set(self.store.load_discovered_tools(self.config.scene)) | set(names))
            return encode({"query": arguments.query, "matched_names": names,
                           "available_from": "next_model_request", "tools": matched}), None, discovered
        if call.name == "say":
            expression = await self.express(turn_id, SayArguments.model_validate(call.arguments),
                                           expression_style=expression_style)
            return self.render(expression), expression, None
        if call.name == "recall_chat":
            return recall_chat(self.store, self.config.scene, self.config.timezone,
                               RecallArguments.model_validate(call.arguments)), None, None
        if call.name == "persona_knowledge":
            arguments = PersonaKnowledgeArguments.model_validate(call.arguments)
            return persona_knowledge(self.persona.id, self.persona.name, self.persona.knowledge,
                                     arguments), None, None
        if call.name == "web_read":
            return await execute_web_read(self.store, self.config.scene, self.config.web_read,
                                          WebReadArguments.model_validate(call.arguments)), None, None
        if call.name == "web_search":
            return await execute_web_search(self.config.web_search,
                                            WebSearchArguments.model_validate(call.arguments)), None, None
        if call.name == "memory":
            return await self.memory.execute(self.config.scene, call.arguments), None, None
        if call.name in {"delegate", "task"}:
            return await execute_tasks(self.tasks, self.config.scene, call.name, call.arguments), None, None
        if call.name in {"schedule", "schedule_list", "schedule_cancel"}:
            return execute_schedule(self.store, self.config, call.name, call.arguments, now=self.now), None, None
        if call.name == "look":
            return await execute_look(
                self.store, self.config.scene, LookArguments.model_validate(call.arguments), self.config.images,
                model_name=self.vision.settings.model, describe=lambda asset: self.describe_image(turn_id, asset),
            ), None, None
        arguments = WaitArguments.model_validate(call.arguments)
        return await wait_for_messages(arguments.seconds), None, None

    async def run_turn(self, *, batch: tuple[int, list[str]] | None,
                       append_new: Callable[[bool, str], Awaitable[bool]],
                       wait_for_messages: Callable[[float], Awaitable[str]],
                       attention_state: dict, scheduled: list[tuple[int, str]] | None = None,
                       task_notices: list[tuple[int, str]] | None = None,
                       direct: bool = False, wake_received_at: float | None = None) -> dict:
        self.direct_request = direct
        scene = self.config.scene
        turn_id = self.store.start_turn(scene, batch=batch, attention_state=attention_state,
                                       scheduled=scheduled, task_notices=task_notices,
                                       wake_received_at=wake_received_at)
        self.notify()
        expressions: list[str] = []
        extensions = 0
        failed_tools = 0
        status, error_text = "step_limit", None
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                recalled = None
                if self.memory is not None and self.memory.settings.auto_recall:
                    recall = await self.memory.recall(scene, self.store.recent_context_messages(scene, limit=8))
                    recalled = encode({"backend": recall["backend"], "items": recall["items"]})
                style = select_style(self.persona)
                expression_style = None
                if style is not None:
                    expression_style = Template((PROMPTS / "next_style.md").read_text()).substitute(
                        name=style.name, note="" if style.note is None else style.note,
                    )
                for step in range(self.config.max_steps):
                    messages = await self.prepare_context(turn_id, expression_style=expression_style, recalled=recalled)
                    reply = await self.request(turn_id, "mind", messages, self.tools)
                    for call in reply.tool_calls:
                        if call.name == "memory":
                            # A tool may have edited or removed the recalled file, even if
                            # a later index update failed. Do not reattach an old excerpt.
                            recalled = None
                        try:
                            content, expression, discovered = await self.execute_tool(
                                turn_id, call, wait_for_messages, expression_style=expression_style,
                            )
                        except Exception as error:
                            failed_tools += 1
                            self.store.complete_tool(scene, call.id, f"{call.name} 失败：{type(error).__name__}: {error}")
                        else:
                            if expression is None:
                                self.store.complete_tool(scene, call.id, content, discovered_tools=discovered)
                            else:
                                content, delivery_status = await self.deliver_expression(
                                    call.id, expression, turn_id=turn_id,
                                )
                                expressions.append(content)
                                if delivery_status not in {"sent", "simulated"}:
                                    failed_tools += 1
                        self.notify()
                    if step + 1 < self.config.max_steps and extensions < self.config.attention.max_extensions:
                        if await append_new(bool(reply.tool_calls), turn_id):
                            extensions += 1
                            continue
                    if not reply.tool_calls:
                        status = "settled"
                        break
        except asyncio.CancelledError:
            self.store.finish_pending_tools(scene, "当前轮被取消")
            self.store.end_turn(turn_id, "cancelled", "CancelledError")
            raise
        except Exception as error:
            status = "timeout" if isinstance(error, TimeoutError) else "error"
            error_text = f"{type(error).__name__}: {error}"
        self.store.finish_pending_tools(scene, error_text or status)
        return {"turn_id": turn_id, "status": status, "error": error_text,
                "delivery": "simulated" if self.send_text is None else "onebot",
                "expressions": expressions, "extensions": extensions,
                "failed_tools": failed_tools}
