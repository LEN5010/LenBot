"""Persistent, scene-serial chat; platform message sending is explicitly injected."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from random import choice
from string import Template
from typing import Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.media.images import image_block
from .config import LabConfig
from .context import (
    CompactionPlan, ContextBudgetError, estimate_content, estimate_request, estimate_text_request,
    plan_compaction, project_history,
)
from .discovery import DEFERRED_NAMES, TOOL_SEARCH, ToolSearchArguments, search_tools
from .delivery import Expression, part_length, report_parts, split_expression
from .expression_selection import ExpressionService
from .external_tools import ExternalTool
from .file_delivery import SEND_FILE_TOOL, SendFileArguments, execute_send_file
from .images import LOOK_TOOL, LookArguments, execute_look
from .audio import TRANSCRIBE_TOOL, TranscribeArguments, AudioService
from .audio_store import AudioStore
from .plugin import Content, Sent
from .plugin_delivery import prepare_parts
from .jargon_store import JargonStore
from .messages import ChatMessage, Segment, Sender, SendResult, UploadResult, plain_text, render_message
from .model import ChatModel, ModelProtocolError, ModelReply, ToolCall
from .model_slots import ModelSlots
from .limits import LimitReached, check_speech
from .memory import MEMORY_TOOL, MemoryService
from .persona import Persona, select_examples, select_style
from .persona_stickers import PersonaSticker
from .sticker_assets import CollectedSticker
from .sticker_store import StickerStore
from .persona_knowledge import PERSONA_KNOWLEDGE_TOOL, PersonaKnowledgeArguments, persona_knowledge
from .platform_tools import (MEMBER_INFO_TOOL, OPEN_FORWARD_TOOL, MemberInfoArguments, OpenForwardArguments,
                             PlatformCall, member_info, open_forward)
from .pricing import estimate_cost
from .recall import RECALL_TOOL, RecallArguments, recall_chat
from .reply_effect_store import ReplyEffectStore
from .schedule import SCHEDULE_TOOLS, describe, execute_schedule
from .skills import Skill
from .store import ImageAsset, Store, encode
from .tasks import WorkTasks
from .tasks_store import TaskStore
from .tasks_tools import DELEGATE_TOOL, TASK_TOOL, execute_tasks
from .web_read import WEB_READ_TOOL, WebReadArguments, execute_web_read
from .web_search import WEB_SEARCH_TOOL, WebSearchArguments, execute_web_search


class SayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1)
    reply_to: str | None = None
    mention: str | None = Field(default=None, pattern=r"^[0-9]+$")
    length: Literal["短", "正常", "长"] = "正常"


class ReactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    emotion: str | None = Field(default=None, min_length=1)
    query: str | None = Field(default=None, min_length=1)
    reply_to: str | None = None

    @model_validator(mode="after")
    def one_selector(self) -> ReactArguments:
        if (self.emotion is None) == (self.query is None):
            raise ValueError("emotion 与 query 必须二选一")
        if not (self.emotion if self.emotion is not None else self.query).strip():
            raise ValueError("表情检索内容不能为空白")
        return self


class MessageSender(Protocol):
    def __call__(self, message: ChatMessage, *, image_bytes: bytes | None = None) -> Awaitable[SendResult]: ...


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
REACT_TOOL = {"type": "function", "function": {
    "name": "react", "description": "在当前场景发送一张表情，角色匹配优先，其次是启用的本群已采用表情；"
        "emotion 精确匹配情绪标签，或 query 按描述、标签字面检索，二选一；结果返回实际来源与发送状态。",
    "parameters": ReactArguments.model_json_schema(),
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
    return [say, REACT_TOOL, WAIT_TOOL, RECALL_TOOL, WEB_SEARCH_TOOL, WEB_READ_TOOL, LOOK_TOOL,
            *SCHEDULE_TOOLS, PERSONA_KNOWLEDGE_TOOL, MEMORY_TOOL, DELEGATE_TOOL, TASK_TOOL,
            SEND_FILE_TOOL, OPEN_FORWARD_TOOL, MEMBER_INFO_TOOL, TRANSCRIBE_TOOL, TOOL_SEARCH]


def tool_unavailable_reasons(config: LabConfig, persona: Persona, name: str) -> list[str]:
    reasons = []
    if persona.tools != "all" and name not in persona.tools:
        reasons.append("当前角色未允许此工具")
    if (name == "react" and not persona.stickers
            and not (config.learning is not None and config.learning.collect_stickers)):
        reasons.append("角色包没有 stickers/ 素材，且未启用本群收集表情")
    if name == "web_read" and config.web_read is None:
        reasons.append("尚未配置网页读取")
    if name == "web_search" and config.web_search is None:
        reasons.append("尚未配置搜索服务")
    if name == "memory" and config.memory is None:
        reasons.append("尚未配置长期记忆后端")
    if name == "look" and config.models.roles.vision is None:
        reasons.append("尚未配置视觉模型")
    if name == "transcribe" and config.models.roles.asr is None:
        reasons.append("尚未配置语音转写模型")
    if name == "schedule" and not config.schedules.enabled:
        reasons.append("当前场景未开启安排")
    if name in {"delegate", "task"} and config.worker is None:
        reasons.append("尚未配置任务容器与 worker 模型")
    if name == "delegate" and not config.tasks.enabled:
        reasons.append("当前场景未开放委托任务")
    if name == "send_file":
        if config.worker is None:
            reasons.append("尚未配置任务交付副本目录")
        if config.onebot is None or config.onebot.upload_visible_root is None:
            reasons.append("尚未配置交付副本在 NapCat 一侧的可见目录")
        if config.delivery != "onebot":
            reasons.append("当前为模拟出口，不执行或伪造文件上传")
    if name == "persona_knowledge" and not persona.knowledge:
        reasons.append("角色包没有 knowledge/ 资料")
    if name in {"open_forward", "member_info", "transcribe"} and config.delivery != "onebot":
        reasons.append("当前为模拟出口，没有可实时查询的平台")
    if name == "member_info" and not config.scene.startswith("group:"):
        reasons.append("只在群场景可用")
    return reasons


def build_tools(config: LabConfig, persona: Persona, *, platform: bool) -> list[dict]:
    if persona.tools != "all":
        for name in ("web_read", "web_search", "look", "persona_knowledge", "memory", "react"):
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
    emotions = sorted({value for sticker in persona.stickers.values() for value in sticker.emotions})
    return [{**tool, "function": {**tool["function"], "description": tool["function"]["description"]
                 + " 当前角色情绪标签：" + encode(emotions)}}
            if tool["function"]["name"] == "react" else tool for tool in allowed]


def voice_prompt(persona: Persona, *, platform: bool) -> str:
    return Template((PROMPTS / "next_voice.md").read_text()).substitute(
        name=persona.name, brief=persona.brief,
        outlet=(PROMPTS / ("next_platform_outlet.md" if platform else "next_simulated_outlet.md")).read_text().strip(),
        self_reference="、".join(persona.self_reference), voice=persona.voice,
        boundaries=persona.boundaries,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)),
    )


def build_system(config: LabConfig, persona: Persona, allowed: list[dict], *, platform: bool,
                 skills: tuple[Skill, ...] = (), group_profile: str | None = None,
                 external: list[dict] = ()) -> str:
    """Render the actual stable mind system text for this scene and outlet."""
    names = {tool["function"]["name"] for tool in allowed}
    deferred = [tool for tool in allowed if tool["function"]["name"] in DEFERRED_NAMES] + list(external)
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
    if group_profile is not None:
        system += "\n" + Template((PROMPTS / "next_group_profile.md").read_text()).substitute(
            profile=group_profile.strip())
    if "react" in names:
        system += "\n" + (PROMPTS / "next_react.md").read_text()
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
        system += "\n" + Template((PROMPTS / "next_tasks.md").read_text()).substitute(
            network=encode({"enabled": config.worker.egress.enabled,
                           "max_task_bytes": (config.worker.egress.max_task_bytes
                               if config.tasks.egress_max_task_bytes is None else config.tasks.egress_max_task_bytes),
                           "max_daily_bytes": (config.worker.egress.max_scene_daily_bytes
                               if config.tasks.egress_max_daily_bytes is None else config.tasks.egress_max_daily_bytes)}),
        )
    if "send_file" in names:
        system += "\n" + (PROMPTS / "next_files.md").read_text()
    if "delegate" in names and skills:
        system += "\n" + Template((PROMPTS / "next_skills.md").read_text()).substitute(
            catalog=encode([{"name": skill.name, "description": skill.description}
                            for skill in skills if not skill.disable_model_invocation]),
        )
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
        self.system = self._base_system

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

    async def request(self, turn_id: str, role: Literal["mind", "voice", "recap", "vision"],
                      messages: list[dict], tools: list[dict], *,
                      recap_target: CompactionPlan | None = None,
                      expression_ids: list[int] | None = None) -> ModelReply:
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
        async with (self.slots.slot(direct=self.direct_request, scene=self.config.scene) if self.slots is not None else nullcontext()):
            call_id = self.store.start_call(turn_id, role, {
                "settings": settings, "messages": messages, "tools": tools,
                "provider": binding.provider,
                **({"expression_ids": expression_ids} if expression_ids is not None else {}),
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
                if role == "voice" and (reply.tool_calls or not reply.text.strip()):
                    raise ValueError(f"表达器未返回完整台词：{encode(reply.message)}")
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

    async def project(self, recap: str | None, entries: list[tuple[int, dict]], state: dict) -> list[dict]:
        profile = None if self.memory is None else await self.memory.read_group_profile(self.config.scene)
        self.system = self.profile_system(profile)
        return [{"role": "system", "content": self.system}] + project_history(recap, entries) + [state]

    def jargon_context(self, messages: list[ChatMessage], *, intent: str | None = None) -> str | None:
        if self.config.learning is None:
            return None
        excluded = (self.config.bot_qq, *self.config.attention.other_bot_qqs)
        texts = [plain_text(message) for message in messages
                 if not message.is_self and message.send_status == "received"
                 and message.sender.uid not in excluded]
        if intent is not None:
            texts.append(intent)
        terms = JargonStore(self.store).matches(self.config.scene, texts, limit=10)
        if not terms:
            return None
        return Template((PROMPTS / "next_jargon_context.md").read_text()).substitute(
            jargon=encode([{"词": item["term"], "含义": item["meaning"]} for item in terms]),
        )

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
            now = datetime.fromtimestamp(self.now(), ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
            state = {"role": "user", "content": f"当前时间：{now}"}
            schedules = self.store.list_schedules(self.config.scene, limit=21)
            if schedules:
                state["content"] += "\n<未完成安排>\n" + "\n\n".join(
                    describe(item, preview=True) for item in schedules[:20]) + "\n</未完成安排>"
                if len(schedules) > 20:
                    state["content"] += "\n这里只列前 20 条；schedule_list 可继续查看。"
            tasks = TaskStore(self.store).list(self.config.scene, limit=21)
            if tasks:
                state["content"] += "\n<未完成工作>\n" + encode([
                    {"id": item.id, "requester": item.requester, "goal": item.goal,
                     "status": item.status, "question": item.question} for item in tasks[:20]]) + "\n</未完成工作>"
                if len(tasks) > 20:
                    state["content"] += "\n这里只列前20项；task可继续查看。"
            if self.config.voice_mode == "direct" and expression_style is not None:
                state["content"] += "\n" + expression_style
            if recalled is not None:
                state["content"] += "\n<相关长期记忆>\n" + recalled + "\n</相关长期记忆>"
            jargon = self.jargon_context(self.store.recent_context_messages(self.config.scene))
            if jargon is not None:
                state["content"] += "\n" + jargon
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
            for message in recent:
                if message.is_self and message.send_status in {"received", "sent", "simulated"}:
                    text = "".join(segment.data["text"] for segment in message.segments if segment.type == "text")
                    messages.append({"role": "assistant", "content": (
                        self.render(message) if any(segment.type == "image" for segment in message.segments)
                        else text)})
                else:
                    rendered = self.render(message)
                    if messages[-1]["role"] == "user":
                        messages[-1]["content"] += "\n" + rendered
                    else:
                        messages.append({"role": "user", "content": rendered})
            task = {"要表达": arguments.content, "长度": arguments.length,
                    "回复对象": None if quote is None else self.render(quote), "平台已负责提及的QQ": arguments.mention}
            if expression_style is not None:
                messages.append({"role": "user", "content": expression_style})
            jargon = self.jargon_context(recent + ([] if quote is None else [quote]), intent=arguments.content)
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
                                                              turn_id=turn_id,
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
                    seq = self.store.start_outgoing(part, image=(None if image is None else
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
