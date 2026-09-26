"""Persistent, scene-serial chat with an explicitly simulated text outlet."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import LabConfig
from .context import (
    CompactionPlan, ContextBudgetError, estimate_content, estimate_request,
    plan_compaction, project_history,
)
from .messages import ChatMessage, Segment, Sender, render_message
from .model import ChatModel, ModelProtocolError, ModelReply, ToolCall
from .persona import Persona
from .recall import RECALL_TOOL, RecallArguments, recall_chat
from .store import Store, encode


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


def voice_prompt(persona: Persona) -> str:
    return Template((PROMPTS / "next_voice.md").read_text()).substitute(
        name=persona.name, brief=persona.brief,
        self_reference="、".join(persona.self_reference), voice=persona.voice,
        boundaries=persona.boundaries,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in persona.examples[:8]),
    )


class Chat:
    def __init__(self, config: LabConfig, persona: Persona, store: Store,
                 mind: ChatModel, voice: ChatModel):
        self.config, self.persona, self.store = config, persona, store
        self.mind, self.voice = mind, voice
        self.tools = [tool for tool in (SAY_TOOL, WAIT_TOOL, RECALL_TOOL)
                      if persona.tools == "all" or tool["function"]["name"] in persona.tools]
        self.tool_names = {tool["function"]["name"] for tool in self.tools}
        # Mode instructions live beside the other prompts, not in runtime branches.
        mode = "next_direct.md" if config.voice_mode == "direct" else "next_intent.md"
        expression_mode = Template((PROMPTS / mode).read_text()).substitute(
            voice=persona.voice,
            examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in persona.examples[:8]))
        self.system = Template((PROMPTS / "next_mind.md").read_text()).substitute(
            name=persona.name, scene=config.scene, bot_qq=config.bot_qq,
            brief=persona.brief, self_reference="、".join(persona.self_reference),
            aliases="、".join(persona.aliases), behavior=persona.behavior,
            boundaries=persona.boundaries, expression_mode=expression_mode,
        )

    def restore(self) -> bool:
        previous = self.store.last_mind_request(self.config.scene)
        if previous is not None:
            current = self.mind.settings.model_dump(exclude={"api_key"})
            if any(previous["settings"][key] != current[key] for key in ("api", "base_url", "model")):
                raise ValueError("大脑模型绑定已改变；此隔离库保留原生续接字段，请显式创建独立会话库")
        resume = self.store.recover(self.config.scene)
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

    async def request(self, turn_id: str, role: Literal["mind", "voice", "recap"],
                      messages: list[dict], tools: list[dict], *,
                      recap_target: CompactionPlan | None = None) -> ModelReply:
        model = self.voice if role == "voice" else self.mind
        binding = self.config.models.roles.voice if role == "voice" else self.config.models.roles.mind
        output_tokens = self.config.compaction.max_output_tokens if role == "recap" else binding.max_output_tokens
        estimated = estimate_request(messages, tools, output_tokens)
        if estimated > binding.context_window_tokens:
            raise ContextBudgetError(
                f"{role} 请求含预留输出估算 {estimated} token，超过配置窗口 {binding.context_window_tokens}；未调用模型")
        settings = model.settings.model_dump(exclude={"api_key"})
        settings["max_output_tokens"] = output_tokens
        call_id = self.store.start_call(turn_id, role, {
            "settings": settings, "messages": messages, "tools": tools,
            "estimated_total_tokens": estimated,
            "context_window_tokens": binding.context_window_tokens,
        })
        reply = None
        try:
            if role == "recap":
                reply = await model.complete(messages, tools, max_output_tokens=output_tokens)
            else:
                reply = await model.complete(messages, tools)
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
            if isinstance(error, ModelProtocolError):
                response, usage = error.response, error.usage
            self.store.end_call(call_id, response, usage, f"{type(error).__name__}: {error}")
            raise
        self.store.end_call(call_id, {"message": reply.message, "finish_reason": reply.finish_reason}, reply.usage,
                            append_to_scene=self.config.scene if role == "mind" else None,
                            recap_for=None if recap_target is None else (self.config.scene, recap_target.through))
        return reply

    def project(self, recap: str | None, entries: list[tuple[int, dict]], state: dict) -> list[dict]:
        return [{"role": "system", "content": self.system}] + project_history(recap, entries) + [state]

    async def prepare_context(self, turn_id: str) -> list[dict]:
        binding = self.config.models.roles.mind
        trigger = int(binding.context_window_tokens * self.config.compaction.trigger_ratio)
        while True:
            recap, entries = self.store.active_history(self.config.scene)
            now = datetime.now(ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
            state = {"role": "user", "content": f"当前时间：{now}"}
            messages = self.project(recap, entries, state)
            if estimate_request(messages, self.tools, binding.max_output_tokens) <= trigger:
                return messages
            plan = plan_compaction(
                entries, system=messages[0], state=state, tools=self.tools,
                output_tokens=binding.max_output_tokens, trigger_tokens=trigger,
                keep_recent_entries=self.config.compaction.keep_recent_entries,
                summary_output_tokens=self.config.compaction.max_output_tokens,
                recap=recap, summary_template=(PROMPTS / "next_recap.md").read_text(),
                window_tokens=binding.context_window_tokens,
            )
            await self.request(turn_id, "recap", plan.request_messages, [], recap_target=plan)

    async def express(self, turn_id: str, arguments: SayArguments) -> ChatMessage:
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
        return ChatMessage(
            id=str(uuid4()), platform="qq", scene=self.config.scene, platform_message_id=None,
            sender=Sender(self.config.bot_qq, self.persona.name, None, None), time=time.time(),
            segments=segments, reply_to=arguments.reply_to, mentions_bot=False,
            is_self=True, send_status="simulated",
        )

    async def execute_tool(self, turn_id: str, call: ToolCall,
                           wait_for_messages: Callable[[float], Awaitable[str]]) -> tuple[str, ChatMessage | None]:
        if call.name not in self.tool_names:
            raise ValueError(f"当前实例未开放工具：{call.name}")
        if call.name == "say":
            expression = await self.express(turn_id, SayArguments.model_validate(call.arguments))
            return self.render(expression), expression
        if call.name == "recall_chat":
            return recall_chat(self.store, self.config.scene, self.config.timezone,
                               RecallArguments.model_validate(call.arguments)), None
        arguments = WaitArguments.model_validate(call.arguments)
        return await wait_for_messages(arguments.seconds), None

    async def run_turn(self, *, batch: tuple[int, list[str]] | None,
                       append_new: Callable[[bool, str], Awaitable[bool]],
                       wait_for_messages: Callable[[float], Awaitable[str]],
                       attention_state: dict) -> dict:
        scene = self.config.scene
        turn_id = self.store.start_turn(scene, batch=batch, attention_state=attention_state)
        expressions: list[str] = []
        extensions = 0
        failed_tools = 0
        status, error_text = "step_limit", None
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                for step in range(self.config.max_steps):
                    messages = await self.prepare_context(turn_id)
                    reply = await self.request(turn_id, "mind", messages, self.tools)
                    for call in reply.tool_calls:
                        try:
                            content, expression = await self.execute_tool(turn_id, call, wait_for_messages)
                        except Exception as error:
                            failed_tools += 1
                            self.store.complete_tool(scene, call.id, f"{call.name} 失败：{type(error).__name__}: {error}")
                        else:
                            result_content = "模拟表达（未发送到 QQ）：" + content if expression is not None else content
                            self.store.complete_tool(scene, call.id, result_content, expression)
                            if expression is not None:
                                expressions.append(content)
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
                "delivery": "simulated", "expressions": expressions, "extensions": extensions,
                "failed_tools": failed_tools}
