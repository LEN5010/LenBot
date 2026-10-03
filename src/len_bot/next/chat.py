"""Persistent, scene-serial chat; platform message sending is explicitly injected."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from random import choice
from string import Template
from typing import Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from len_bot.media.images import image_block
from .chat_context import PROMPTS, build_system, jargon_context, turn_state, voice_prompt
from .chat_tools import ReactArguments, SayArguments, WaitArguments, build_tools, tool_catalog
from .config import LabConfig
from .context import (
    CompactionPlan, ContextBudgetError, estimate_content, estimate_request,
    plan_compaction, project_history,
)
from .discovery import DEFERRED_NAMES, ToolSearchArguments, search_tools
from .delivery import Expression, part_length, report_parts, split_expression
from .expression_selection import ExpressionService
from .external_tools import ExternalTool
from .file_delivery import SendFileArguments, execute_send_file
from .images import LookArguments, execute_look
from .audio import TranscribeArguments, AudioService
from .audio_store import AudioStore
from .plugin import Content, Sent
from .plugin_delivery import prepare_parts
from .messages import ChatMessage, Segment, Sender, SendResult, UploadResult, render_message, render_text
from .model import ChatModel, ModelReply, ToolCall
from .model_slots import ModelSlots
from .limits import LimitReached, check_speech
from .memory import MemoryService
from .persona import Persona, select_style
from .persona_stickers import PersonaSticker
from .sticker_assets import CollectedSticker
from .sticker_store import StickerStore
from .persona_knowledge import PersonaKnowledgeArguments, persona_knowledge
from .platform_tools import (MemberInfoArguments, OpenForwardArguments,
                             PlatformCall, member_info, open_forward)
from .model_request import request_model
from .recall import RecallArguments, recall_chat
from .reply_effect_store import ReplyEffectStore
from .scene_control import SceneControlArguments
from .schedule import execute_schedule
from .store import ImageAsset, Store, encode
from .tasks import WorkTasks
from .tasks_store import TaskStore
from .tasks_tools import execute_tasks
from .web_read import WebReadArguments, execute_web_read
from .web_search import WebSearchArguments, execute_web_search
from .replay_web import RecordedWeb
from .replay_images import RecordedImages


class MessageSender(Protocol):
    def __call__(self, message: ChatMessage, *, image_bytes: bytes | None = None) -> Awaitable[SendResult]: ...


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
        self.replay_web = None if config.replay_web is None else RecordedWeb(config.replay_web)
        self.replay_images = (None if config.replay_images is None else
                              RecordedImages(config.replay_images, max_bytes=config.images.max_bytes))
        self.now = now
        self.mind, self.voice, self.vision = mind, voice, vision
        self.audio = audio_service
        if (config.memory is None) != (memory is None):
            raise ValueError("memory 服务必须与根配置的记忆后端一起提供")
        self.memory = memory
        if (config.worker is None) != (tasks is None):
            raise ValueError("任务服务必须与根配置的 worker 一起提供")
        self.tasks = tasks
        self.expression_service = expression_service
        self.slots = slots
        self.direct_request = False
        # Actual wake channels seen by the current turn, snapshotted per expression.
        self.turn_channels: set[str] = set()
        self.send_message = send_message
        self.upload_file = upload_file
        self.platform_call = platform_call
        self.on_update = on_update
        self.on_compaction = on_compaction
        self.on_reply_sample = on_reply_sample
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
        self.scene_control: Callable[[SceneControlArguments], dict] | None = None
        self.skills = () if tasks is None else tasks.skills[config.scene]
        self.set_external_tools(external_tools)
        self.system = self.profile_system(None if memory is None else memory.group_profile(config.scene))
        # One outlet per scene: plugin sends never interleave with a multi-part expression.
        self.outlet = asyncio.Lock()

    def set_external_tools(self, external_tools: list[ExternalTool]) -> None:
        """Replace the actual external capability set after service startup/closure."""
        config, persona = self.config, self.persona
        send_message, upload_file, platform_call = self.send_message, self.upload_file, self.platform_call
        allowed = build_tools(config, persona, platform=send_message is not None)
        if self.scene_control is None:
            allowed = [tool for tool in allowed if tool["function"]["name"] != "scene_control"]
        # Plugin and MCP tools are always low-frequency and still need the role's permission.
        self.external = {tool.name: tool for tool in external_tools
                         if persona.tools == "all" or tool.name in persona.tools}
        clashes = sorted({tool["function"]["name"] for tool in tool_catalog(platform=True)} & set(self.external))
        if clashes:
            raise ValueError(f"插件或 MCP 工具与核心工具重名：{clashes}")
        if self.external and "tool_search" not in {tool["function"]["name"] for tool in allowed}:
            raise ValueError("角色开放插件或 MCP 工具时必须同时开放 tool_search")
        self.allowed_tool_names = {tool["function"]["name"] for tool in allowed} | set(self.external)
        if "send_file" in self.allowed_tool_names and upload_file is None:
            raise ValueError("send_file 配置已启用但未接入实际文件上传出口")
        if self.allowed_tool_names & {"open_forward", "member_info"} and platform_call is None:
            raise ValueError("平台查询工具已启用但未接入实际 OneBot 调用")
        if "transcribe" in self.allowed_tool_names and self.audio is None:
            raise ValueError("语音工具已启用但未接入实际语音处理服务")
        self.deferred_names = DEFERRED_NAMES | set(self.external)
        self.core_tools = [tool for tool in allowed if tool["function"]["name"] not in DEFERRED_NAMES]
        self.deferred_tools = ([tool for tool in allowed if tool["function"]["name"] in DEFERRED_NAMES]
                               + [tool.definition for tool in self.external.values()])
        saved = self.store.load_discovered_tools(config.scene)
        self.discovered_tools = set(saved) & self.allowed_tool_names & self.deferred_names
        self._base_system = build_system(config, persona, allowed, platform=send_message is not None,
                                   skills=self.skills,
                                   external=[tool.definition for tool in self.external.values()])
        self.system = self.profile_system(None if self.memory is None else self.memory.group_profile(config.scene))

    def profile_system(self, profile: str | None) -> str:
        if profile is None:
            return self._base_system
        return self._base_system + "\n" + Template((PROMPTS / "next_group_profile.md").read_text()).substitute(
            profile=profile.strip())

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
                "本次启动已更新角色、表达模式或本群记忆概览；当前系统设定生效，已有聊天原文保留。"})
        history = self.store.recent(self.config.scene, 1)
        if history:
            last = datetime.fromtimestamp(history[-1].time, ZoneInfo(self.config.timezone)).isoformat()
            self.store.append(self.config.scene, {"role": "user", "content":
                f"隔离会话已恢复。上次保存聊天时间：{last}。离线期间的消息尚未取得。"})
        return resume

    def render(self, message: ChatMessage) -> str:
        quote = (None if message.reply_to is None else
                 self.store.find_message(message.scene, message.reply_to))
        return render_message(message, timezone=self.config.timezone, reply=quote,
                              audio=AudioStore(self.store).captions(message.scene, message.platform_message_id))

    def render_text(self, message: ChatMessage) -> str:
        quote = (None if message.reply_to is None else
                 self.store.find_message(message.scene, message.reply_to))
        return render_text(message, reply=quote,
                           audio=AudioStore(self.store).captions(message.scene, message.platform_message_id))

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

    async def project(self, recap: str | None, entries: list[tuple[int, dict]], state: dict) -> list[dict]:
        profile = None if self.memory is None else await self.memory.read_group_profile(self.config.scene)
        self.system = self.profile_system(profile)
        return [{"role": "system", "content": self.system}] + project_history(recap, entries) + [state]

    async def compact_now(self) -> dict:
        """One explicit operator-requested compaction; caller owns the scene lock."""
        recap, entries = self.store.active_history(self.config.scene)
        binding = self.config.models.roles.mind
        state = {"role": "user", "content": "运营者请求压缩已有完整对话，原始记录保留。"}
        plan = plan_compaction(
            entries, system={"role": "system", "content": self.system}, state=state,
            tools=self.core_tools, output_tokens=binding.max_output_tokens,
            trigger_tokens=int(binding.context_window_tokens * self.config.compaction.trigger_ratio),
            keep_recent_entries=self.config.compaction.keep_recent_entries,
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

    async def prepare_context(self, turn_id: str, *, expression_style: str | None = None,
                              recalled: str | None = None) -> list[dict]:
        binding = self.config.models.roles.mind
        trigger = int(binding.context_window_tokens * self.config.compaction.trigger_ratio)
        while True:
            AudioStore(self.store).append_late(self.config.scene)
            recap, entries = self.store.active_history(self.config.scene)
            # Refresh only between model requests, never midway through a tool group.
            self.discovered_tools = set(self.store.load_discovered_tools(self.config.scene))
            state = turn_state(self.config, self.store, now=self.now(),
                               expression_style=expression_style, recalled=recalled)
            messages = await self.project(recap, entries, state)
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

    def check_limits(self, *, model: bool = False) -> None:
        check_speech(self.store, self.config)
        if model and self.slots is not None and self.slots.admit is not None:
            self.slots.admit(self.config.scene)

    async def express(self, turn_id: str, arguments: SayArguments, *,
                      expression_style: str | None = None) -> ChatMessage:
        self.check_limits()
        quote = None
        if arguments.reply_to is not None:
            quote = self.store.find_message(self.config.scene, arguments.reply_to)
            if quote is None:
                raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if self.config.voice_mode == "direct":
            text = arguments.content
        else:
            messages = [{"role": "system", "content": voice_prompt(self.persona, platform=self.config.delivery == "onebot")}]
            recent = self.store.recent_context_messages(self.config.scene)
            messages.append({"role": "user", "content": "<群聊对话稿>\n"
                             + "\n".join(self.render(message) for message in recent) + "\n</群聊对话稿>"})
            task = {"表达意图": arguments.content, "长度": arguments.length,
                    "回复对象": None if quote is None else self.render(quote), "平台已负责提及的QQ": arguments.mention}
            if expression_style is not None:
                messages.append({"role": "user", "content": expression_style})
            jargon = jargon_context(self.config, self.store, recent + ([] if quote is None else [quote]),
                                    intent=arguments.content)
            if jargon is not None:
                messages.append({"role": "user", "content": jargon})
            selected = []
            if self.expression_service is not None and self.config.scene in self.expression_service.scenes:
                selected = await self.expression_service.select(
                    self.config.scene, arguments.content, turn_id=turn_id, direct=self.direct_request,
                )
            if selected:
                messages.append({"role": "user", "content": Template(
                    (PROMPTS / "next_learned_expressions.md").read_text(),
                ).substitute(expressions=encode([
                    {"情境": item["situation"], "说法": item["style"]} for item in selected
                ]))})
            messages.append({"role": "user", "content": encode(task)})
            reply = await self.request(turn_id, "voice", messages, [],
                                       expression_ids=[item["id"] for item in selected])
            text = reply.text
        segments = []
        platform_reply = (arguments.reply_to if quote is not None and (
            self.now() - quote.time > 120 or self.store.messages_after(self.config.scene, arguments.reply_to) > 3
        ) else None)
        if platform_reply is not None:
            segments.append(Segment("reply", {"id": platform_reply}))
        if arguments.mention is not None:
            segments.append(Segment("at", {"qq": arguments.mention}))
        segments.append(Segment("text", {"text": text}))
        return self.simulated_message(segments, reply_to=platform_reply)

    def react(self, arguments: ReactArguments) -> Expression:
        if arguments.reply_to is not None and self.store.find_message(self.config.scene, arguments.reply_to) is None:
            raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if arguments.emotion is not None:
            matches = [sticker for sticker in self.persona.stickers.values()
                       if arguments.emotion.casefold() in {value.casefold() for value in sticker.emotions}]
        else:
            query = arguments.query.casefold()
            matches = [sticker for sticker in self.persona.stickers.values()
                       if any(query in value.casefold() for value in
                              (sticker.description, *sticker.emotions, *sticker.tags))]
        if matches:
            usage = self.store.sticker_usage(self.config.scene, self.persona.id)
            least = min(usage.get(sticker.file, 0) for sticker in matches)
            sticker = choice([sticker for sticker in matches if usage.get(sticker.file, 0) == least])
            summary = f"角色表情 {sticker.file}：{sticker.description}"
        elif self.config.learning is not None and self.config.learning.collect_stickers:
            records = StickerStore(self.store)
            collected = records.matches(self.config.scene, emotion=arguments.emotion, query=arguments.query)
            if not collected:
                raise ValueError(f"角色及本群已采用表情无匹配：{encode(arguments.model_dump(exclude_none=True))}")
            least = min(item["uses"] for item in collected)
            selected = choice([item for item in collected if item["uses"] == least])
            sticker = records.asset(self.config.scene, selected["id"])
            source = self.store.read_message(self.config.scene, sticker.source_message_seq)
            summary = (f"本群表情，来源平台消息 {source.platform_message_id} 第{sticker.source_image_index}张："
                       f"{sticker.description}")
        else:
            raise ValueError(f"角色表情无匹配：{encode(arguments.model_dump(exclude_none=True))}")
        segments = []
        if arguments.reply_to is not None:
            segments.append(Segment("reply", {"id": arguments.reply_to}))
        segments.append(Segment("image", {"summary": summary}))
        return Expression(self.simulated_message(segments, reply_to=arguments.reply_to), sticker)

    async def deliver_expression(self, call_id: str, expression: Expression, *,
                                 turn_id: str) -> tuple[str, str]:
        parts = ([expression.message] if expression.sticker is not None else
                 split_expression(expression.message, self.config.text_delivery.max_chars))
        entry_seq = self.store.prepare_expression(
            self.config.scene, call_id, report_parts(parts, [], self.render),
        )
        prefix = "模拟表达（未发送到 QQ）：" if self.send_message is None else ""
        return await self.send_prepared_expression(entry_seq, parts, prefix=prefix, turn_id=turn_id,
                                                   sticker=expression.sticker)

    async def send_prepared_expression(self, entry_seq: int, parts: list[ChatMessage],
                                       *, prefix: str = "", turn_id: str | None = None,
                                       sticker: PersonaSticker | CollectedSticker | None = None,
                                       channels: set[str] | None = None, quota_notice: bool = False) -> tuple[str, str]:
        async with self.outlet:
            return await self._send_prepared(entry_seq, parts, prefix=prefix, turn_id=turn_id,
                                             sticker=sticker, channels=channels, quota_notice=quota_notice)

    async def _send_prepared(self, entry_seq: int, parts: list[ChatMessage], *, prefix: str,
                             turn_id: str | None, sticker: PersonaSticker | CollectedSticker | None,
                             channels: set[str] | None, quota_notice: bool) -> tuple[str, str]:
        errors: list[str | None] = []
        settings = self.config.text_delivery
        # Snapshot now: a later append in this turn does not change why this was said.
        seen = set(self.turn_channels if channels is None else channels)
        confirmed: list[tuple[int, float]] = []
        try:
            for index, part in enumerate(parts):
                if index:
                    delay = min(settings.max_interval_seconds,
                                max(settings.min_interval_seconds, part_length(part) / settings.chars_per_second))
                    await asyncio.sleep(delay)
                if not quota_notice:
                    try:
                        self.check_limits(model=False)
                    except LimitReached as error:
                        content = prefix + report_parts(parts, errors, self.render) + "\n" + str(error)
                        self.store.expression_error(entry_seq, str(error))
                        raise
                part.time = self.now()
                part.send_status = "simulated" if self.send_message is None else "unconfirmed"
                errors.append(None)
                content = report_parts(parts, errors, self.render)
                message_seq = self.store.start_expression_part(entry_seq, part, prefix + content,
                                                              persona_id=self.persona.id, turn_id=turn_id,
                                                              sticker=(sticker if isinstance(sticker, CollectedSticker)
                                                                       else None if sticker is None
                                                                       else (self.persona.id, sticker)))
                self.notify()
                if self.send_message is not None:
                    result = await self.send_message(part, image_bytes=None if sticker is None else sticker.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
                    content = report_parts(parts, errors, self.render)
                    kept = self.store.finish_expression((message_seq, entry_seq), part, prefix + content,
                                                        turn_id=turn_id)
                    if result.status == "sent":
                        confirmed.append((kept, self.now()))
                    self.notify()
                    if result.status != "sent":
                        break
        finally:
            if (confirmed and self.config.learning is not None and self.config.learning.reply_effects):
                ReplyEffectStore(self.store).record(
                    self.config.scene, entry_seq, turn_id=turn_id, channels=sorted(seen),
                    sent=confirmed, planned_parts=len(parts),
                )
                if self.on_reply_sample is not None:
                    self.on_reply_sample()
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return content, status

    async def send_plugin_content(self, plugin: str, content: Sequence[Content], *, reply_to: str | None) -> Sent:
        """Send ordered plugin content through one scene outlet and retain actual results."""
        if reply_to is not None and self.store.find_message(self.config.scene, reply_to) is None:
            raise ValueError(f"当前场景没有平台消息 {reply_to}")
        self.check_limits(model=False)
        prepared = await prepare_parts(plugin, content, self.config.text_delivery.max_chars, reply_to)
        parts = [self.simulated_message(part.segments, reply_to=reply_to if index == 0 else None)
                 for index, part in enumerate(prepared)]
        settings = self.config.text_delivery
        errors: list[str | None] = []
        interruption: str | None = None
        async with self.outlet:
            try:
                for index, part in enumerate(parts):
                    if index:
                        await asyncio.sleep(min(settings.max_interval_seconds, max(
                            settings.min_interval_seconds, part_length(part) / settings.chars_per_second)))
                    self.check_limits(model=False)
                    part.time = self.now()
                    part.send_status = "simulated" if self.send_message is None else "unconfirmed"
                    errors.append(None)
                    image = prepared[index].image
                    seq = self.store.start_outgoing(part, persona_id=self.persona.id, image=(None if image is None else
                                                    (image, prepared[index].description)))
                    self.notify()
                    if self.send_message is None:
                        continue
                    result = await self.send_message(part, image_bytes=None if image is None else image.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
                    self.store.finish_expression((seq, None), part, "")
                    self.notify()
                    if result.status != "sent":
                        break
            except BaseException as error:
                interruption = f"{type(error).__name__}: {error}"
                raise
            finally:
                if errors:
                    report = report_parts(parts, errors, self.render)
                    if interruption is not None:
                        report += "\n" + interruption
                    moment = datetime.fromtimestamp(self.now(), ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
                    self.store.add_plugin_event(self.config.scene, plugin, "reply", Template(
                        (PROMPTS / "next_plugin_reply.md").read_text()).substitute(
                        plugin=plugin, time=moment,
                        report=("模拟表达（未发送到 QQ）：" if self.send_message is None else "") + report).strip())
                    self.notify()
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return Sent(status, report, tuple(part.platform_message_id for part in parts[:len(errors)]
                                         if part.send_status == "sent"))

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
                           ) -> tuple[str, Expression | None, list[str] | None]:
        if call.name not in self.tool_names:
            raise ValueError(f"当前请求未开放工具：{call.name}")
        if call.name == "tool_search":
            arguments = ToolSearchArguments.model_validate(call.arguments)
            matched = search_tools(arguments.query, self.deferred_tools)
            names = [tool["function"]["name"] for tool in matched]
            discovered = sorted(set(self.store.load_discovered_tools(self.config.scene)) | set(names))
            return encode({"query": arguments.query, "matched_names": names,
                           "available_from": "next_model_request", "tools": matched}), None, discovered
        if call.name in self.external:
            tool = self.external[call.name]
            return await tool.call(self.config.scene, call.arguments), None, None
        if call.name == "say":
            expression = await self.express(turn_id, SayArguments.model_validate(call.arguments),
                                           expression_style=expression_style)
            return self.render(expression), Expression(expression), None
        if call.name == "react":
            expression = self.react(ReactArguments.model_validate(call.arguments))
            return self.render(expression.message), expression, None
        if call.name == "scene_control":
            return encode(self.scene_control(SceneControlArguments.model_validate(call.arguments))), None, None
        if call.name == "recall_chat":
            return recall_chat(self.store, self.config.scene, self.config.timezone,
                               RecallArguments.model_validate(call.arguments)), None, None
        if call.name == "persona_knowledge":
            arguments = PersonaKnowledgeArguments.model_validate(call.arguments)
            return persona_knowledge(self.persona.id, self.persona.name, self.persona.knowledge,
                                     arguments), None, None
        if call.name == "web_read":
            return await execute_web_read(self.store, self.config.scene, self.config.web_read,
                                          WebReadArguments.model_validate(call.arguments), recording=self.replay_web), None, None
        if call.name == "web_search":
            return await execute_web_search(self.config.web_search,
                                            WebSearchArguments.model_validate(call.arguments), recording=self.replay_web), None, None
        if call.name == "memory":
            return await self.memory.execute(self.config.scene, call.arguments), None, None
        if call.name in {"delegate", "task"}:
            return encode(await execute_tasks(self.tasks, self.config.scene, call.name, call.arguments)), None, None
        if call.name == "send_file":
            return await execute_send_file(
                TaskStore(self.store), self.config.scene, SendFileArguments.model_validate(call.arguments),
                local_root=self.config.worker.delivery_root,
                visible_root=self.config.onebot.upload_visible_root,
                upload=self.upload_file, on_update=self.notify,
            ), None, None
        if call.name in {"schedule", "schedule_list", "schedule_cancel"}:
            return execute_schedule(self.store, self.config, call.name, call.arguments, now=self.now), None, None
        if call.name == "open_forward":
            return await open_forward(self.store, self.config.scene, self.config.timezone,
                                      OpenForwardArguments.model_validate(call.arguments), self.platform_call), None, None
        if call.name == "member_info":
            return await member_info(self.config.scene, self.config.timezone,
                                     MemberInfoArguments.model_validate(call.arguments), self.platform_call), None, None
        if call.name == "look":
            return await execute_look(
                self.store, self.config.scene, LookArguments.model_validate(call.arguments), self.config.images,
                model_name=self.vision.settings.model, describe=lambda asset: self.describe_image(turn_id, asset),
                recording=self.replay_images,
            ), None, None
        if call.name == "transcribe":
            return await self.audio.transcribe(
                self.config.scene, TranscribeArguments.model_validate(call.arguments),
                turn_id=turn_id, direct=self.direct_request,
            ), None, None
        arguments = WaitArguments.model_validate(call.arguments)
        return await wait_for_messages(arguments.seconds), None, None

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
                            if isinstance(error, LimitReached):
                                raise
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
            status = "limited" if isinstance(error, LimitReached) else "timeout" if isinstance(error, TimeoutError) else "error"
            limit_until = error.until if isinstance(error, LimitReached) else None
            error_text = f"{type(error).__name__}: {error}"
        self.store.finish_pending_tools(scene, error_text or status)
        return {"turn_id": turn_id, "status": status, "error": error_text,
                "delivery": "simulated" if self.send_message is None else "onebot",
                "expressions": expressions, "extensions": extensions,
                "failed_tools": failed_tools, "limit_until": limit_until}
