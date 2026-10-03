"""Persistent scene-serial model loop, composing context, tools and expression."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from string import Template
from typing import Literal
from zoneinfo import ZoneInfo

from ..media.audio import AudioService
from ..media.audio_store import AudioStore
from .context import ChatContext, PROMPTS, turn_state
from .expression import ChatExpression, MessageSender
from .tools import SceneTools
from ..config import LabConfig
from .recap import CompactionPlan, ContextBudgetError, estimate_content, plan_compaction
from ..learning.expression_selection import ExpressionService
from ..tools.external_tools import ExternalTool
from ..models.limits import LimitReached, check_speech
from ..memory.service import MemoryService
from ..platform.messages import UploadResult
from ..models.client import ChatModel, ModelReply
from ..models.request import request_estimate, request_model
from ..models.slots import ModelSlots
from ..persona.profile import Persona, select_style
from ..platform.platform_tools import PlatformCall
from ..storage.store import Store, encode
from ..work.service import WorkTasks


class Chat:
    def __init__(self, config: LabConfig, persona: Persona, store: Store,
                 mind: ChatModel, voice: ChatModel, *,
                 vision: ChatModel | None = None,
                 memory: MemoryService | None = None,
                 tasks: WorkTasks | None = None,
                 expression_service: ExpressionService | None = None,
                 slots: ModelSlots | None = None,
                 send_message: MessageSender | None = None,
                 upload_file: Callable[[str, str, str], Awaitable[UploadResult]] | None = None,
                 platform_call: PlatformCall | None = None,
                 external_tools: list[ExternalTool] = (),
                 audio_service: AudioService | None = None,
                 on_update: Callable[[], None] | None = None,
                 on_compaction: Callable[[], None] | None = None,
                 on_reply_sample: Callable[[], None] | None = None,
                 now: Callable[[], float] = time.time):
        self.config, self.persona, self.store = config, persona, store
        self.now = now
        self.mind, self.voice, self.vision = mind, voice, vision
        self.audio = audio_service
        if (config.memory is None) != (memory is None):
            raise ValueError("memory 服务必须与根配置的记忆后端一起提供")
        self.memory = memory
        if (config.worker is None) != (tasks is None):
            raise ValueError("任务服务必须与根配置的 worker 一起提供")
        self.tasks = tasks
        self.slots = slots
        self.direct_request = False
        # Actual wake channels seen by the current turn, snapshotted per expression.
        self.turn_channels: set[str] = set()
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
        self.skills = () if tasks is None else tasks.skills[config.scene]
        self.context = ChatContext(config, persona, store, platform=send_message is not None, memory=memory)
        self.expression = ChatExpression(
            config, persona, store, context=self.context, request=self.request,
            send_message=send_message, expression_service=expression_service,
            notify=self.notify, on_reply_sample=on_reply_sample, now=now)
        self.toolset = SceneTools(
            config, persona, store, expression=self.expression, request=self.request, vision=vision,
            memory=memory, tasks=tasks, audio=audio_service, upload_file=upload_file,
            platform_call=platform_call, notify=self.notify, now=now)
        self.set_external_tools(external_tools)

    def set_external_tools(self, external_tools: list[ExternalTool]) -> None:
        """Refresh both the available schemas and their system-prompt descriptions."""
        allowed = self.toolset.set_external_tools(external_tools)
        self.context.configure_tools(allowed, [tool.definition for tool in self.toolset.external.values()],
                                     skills=self.skills)

    def notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    def restore(self) -> bool:
        previous = self.store.last_mind_request(self.config.scene)
        resume = self.store.recover(self.config.scene)
        if set(self.store.load_discovered_tools(self.config.scene)) != self.toolset.discovered_tools:
            self.store.save_discovered_tools(self.config.scene, sorted(self.toolset.discovered_tools))
        if previous is not None and previous["messages"][0]["content"] != self.context.system:
            self.store.append(self.config.scene, {"role": "user", "content":
                "本次启动已更新角色、表达模式或工具能力；当前系统设定生效，已有聊天原文保留。"})
        history = self.store.recent(self.config.scene, 1)
        if history:
            last = datetime.fromtimestamp(history[-1].time, ZoneInfo(self.config.timezone)).isoformat()
            self.store.append(self.config.scene, {"role": "user", "content":
                f"隔离会话已恢复。上次保存聊天时间：{last}。离线期间的消息尚未取得。"})
        return resume

    async def request(self, turn_id: str, role: Literal["mind", "voice", "recap", "vision"],
                      messages: list[dict], tools: list[dict], *,
                      recap_target: CompactionPlan | None = None,
                      expression_ids: list[int] | None = None) -> ModelReply:
        model_role = "mind" if role == "recap" else role
        model = getattr(self, model_role)
        def validate(reply: ModelReply) -> None:
            if role in {"voice", "vision"} and (reply.tool_calls or not reply.text.strip()):
                label = "表达器未返回完整台词" if role == "voice" else "视觉模型未返回完整描述"
                raise ValueError(f"{label}：{encode(reply.message)}")
            if recap_target is not None:
                if reply.tool_calls or not reply.text.strip():
                    raise ValueError(f"压缩模型未返回完整回想：{encode(reply.message)}")
                content_tokens = estimate_content(reply.text)
                if content_tokens > recap_target.summary_budget_tokens:
                    raise ContextBudgetError(
                        f"新回想正文估算 {content_tokens} token，超过本步回想预算 "
                        f"{recap_target.summary_budget_tokens}；未切换起点")

        return await request_model(
            self.config, self.store, model, messages, tools, scene=self.config.scene, role=role,
            turn_id=turn_id, slots=self.slots, direct=self.direct_request,
            output_tokens=self.config.compaction.max_output_tokens if role == "recap" else None,
            validate=validate, append_to_scene=role == "mind",
            recap_for=None if recap_target is None else (self.config.scene, recap_target.through),
            expression_ids=expression_ids, notify=self.notify)

    async def compact_now(self) -> dict:
        """One explicit operator-requested compaction; caller owns the scene lock."""
        recap, entries = self.store.active_history(self.config.scene)
        binding = self.config.models.roles.mind
        state = {"role": "user", "content": "运营者请求压缩已有完整对话，原始记录保留。"}
        plan = plan_compaction(
            entries, system={"role": "system", "content": self.context.system}, state=state,
            tools=self.toolset.core_tools, output_tokens=binding.max_output_tokens,
            trigger_tokens=int(binding.context_window_tokens * self.config.compaction.trigger_ratio),
            keep_recent_tokens=self.config.compaction.keep_recent_tokens,
            summary_output_tokens=self.config.compaction.max_output_tokens, recap=recap,
            summary_template=(PROMPTS / "next_recap.md").read_text(), window_tokens=binding.context_window_tokens,
        )
        turn_id = self.store.start_turn(self.config.scene)
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                reply = await self.request(turn_id, "recap", plan.request_messages, [], recap_target=plan)
        except BaseException as error:
            self.store.end_turn(turn_id, "error", f"{type(error).__name__}: {error}")
            raise
        self.store.end_turn(turn_id, "manual_compaction")
        if self.on_compaction is not None:
            self.on_compaction()
        self.notify()
        return {"turn_id": turn_id, "compact_through": plan.through, "recap": reply.text}

    async def prepare_context(self, turn_id: str, *, observed_at: float, expression_style: str | None = None,
                              recalled: str | None = None) -> list[dict]:
        binding = self.config.models.roles.mind
        trigger = int(binding.context_window_tokens * self.config.compaction.trigger_ratio)
        while True:
            AudioStore(self.store).append_late(self.config.scene)
            recap, entries = self.store.active_history(self.config.scene)
            # Refresh only between model requests, never midway through a tool group.
            self.toolset.discovered_tools = set(self.store.load_discovered_tools(self.config.scene))
            state = turn_state(self.config, self.store, now=observed_at,
                               expression_style=expression_style, recalled=recalled)
            messages = await self.context.project(recap, entries, state)
            estimated, _ = request_estimate(self.config, self.store, self.mind, scene=self.config.scene,
                                            role='mind', messages=messages, tools=self.toolset.tools,
                                            output_tokens=binding.max_output_tokens)
            if estimated <= trigger:
                return messages
            plan = plan_compaction(
                entries, system=messages[0], state=messages[-1], tools=self.toolset.core_tools,
                output_tokens=binding.max_output_tokens, trigger_tokens=trigger,
                keep_recent_tokens=self.config.compaction.keep_recent_tokens,
                summary_output_tokens=self.config.compaction.max_output_tokens,
                recap=recap, summary_template=(PROMPTS / "next_recap.md").read_text(),
                window_tokens=binding.context_window_tokens,
            )
            await self.request(turn_id, "recap", plan.request_messages, [], recap_target=plan)
            if self.on_compaction is not None:
                self.on_compaction()

    def check_limits(self, *, model: bool = False) -> None:
        check_speech(self.store, self.config)
        if model and self.slots is not None and self.slots.admit is not None:
            self.slots.admit(self.config.scene)

    async def run_turn(self, *, batch: tuple[int, list[str]] | None,
                       append_new: Callable[[bool, str], Awaitable[bool]],
                       wait_for_messages: Callable[[float], Awaitable[str]],
                       attention_state: dict, scheduled: list[tuple[int, str]] | None = None,
                       task_notices: list[tuple[int, str]] | None = None,
                       plugin_events: list[tuple[int, str]] | None = None,
                       direct: bool = False, wake_received_at: float | None = None,
                       channels: set[str] | None = None,
                       proactive: tuple[str, str, float] | None = None) -> dict:
        self.direct_request = direct
        observed_at = self.now()
        self.turn_channels = set() if channels is None else set(channels)
        scene = self.config.scene
        turn_id = self.store.start_turn(scene, batch=batch, attention_state=attention_state,
                                       scheduled=scheduled, task_notices=task_notices,
                                       plugin_events=plugin_events,
                                       wake_received_at=wake_received_at, proactive=proactive)
        self.notify()
        expressions: list[str] = []
        extensions = 0
        failed_tools = 0
        status, error_text = "step_limit", None
        limit_until = None
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
                    self.check_limits()
                    messages = await self.prepare_context(turn_id, observed_at=observed_at,
                                                          expression_style=expression_style, recalled=recalled)
                    reply = await self.request(turn_id, "mind", messages, self.toolset.tools)
                    end_turn, group_failed = False, False
                    for call in reply.tool_calls:
                        if call.name == "memory":
                            # A tool may have edited or removed the recalled file, even if
                            # a later index update failed. Do not reattach an old excerpt.
                            recalled = None
                        try:
                            content, expression, discovered = await self.toolset.execute(
                                turn_id, call, wait_for_messages, expression_style=expression_style, direct=self.direct_request,
                            )
                        except Exception as error:
                            group_failed = True
                            failed_tools += 1
                            self.store.complete_tool(scene, call.id, f"{call.name} 失败：{type(error).__name__}: {error}")
                            if isinstance(error, LimitReached):
                                raise
                        else:
                            if expression is None:
                                self.store.complete_tool(scene, call.id, content, discovered_tools=discovered)
                            else:
                                content, delivery_status = await self.expression.deliver_expression(
                                    call.id, expression, turn_id=turn_id, channels=self.turn_channels,
                                )
                                expressions.append(content)
                                if delivery_status in {"sent", "simulated"}:
                                    end_turn = end_turn or expression.end_turn
                                else:
                                    group_failed = True
                                    failed_tools += 1
                        self.notify()
                    if step + 1 < self.config.max_steps and extensions < self.config.attention.max_extensions:
                        if await append_new(bool(reply.tool_calls), turn_id):
                            extensions += 1
                            continue
                    if not reply.tool_calls or (end_turn and not group_failed):
                        status = "settled"
                        break
        except asyncio.CancelledError:
            self.store.finish_pending_tools(scene, "当前轮被取消")
            self.store.end_turn(turn_id, "cancelled", "CancelledError")
            raise
        except Exception as error:
            status = "limited" if isinstance(error, LimitReached) else "timeout" if isinstance(error, TimeoutError) else "error"
            limit_until = error.until if isinstance(error, LimitReached) else None
            error_text = f"{type(error).__name__}: {error}"
        self.store.finish_pending_tools(scene, error_text or status)
        return {"turn_id": turn_id, "status": status, "error": error_text,
                "delivery": "simulated" if self.expression.send_message is None else "onebot",
                "expressions": expressions, "extensions": extensions,
                "failed_tools": failed_tools, "limit_until": limit_until}
