"""Core tool schemas, scene capability discovery and direct domain dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.media.images import image_block

from ..media.audio import TRANSCRIBE_TOOL, AudioService, TranscribeArguments
from .context import PROMPTS
from ..config import LabConfig
from ..tools.discovery import DEFERRED_NAMES, TOOL_SEARCH, ToolSearchArguments, model_schema, search_tools
from ..platform.delivery import Expression
from ..tools.external_tools import ExternalTool
from ..platform.file_delivery import SEND_FILE_TOOL, SendFileArguments, execute_send_file
from ..media.images import LOOK_TOOL, LookArguments, execute_look
from ..memory.service import MEMORY_TOOL, MemoryService
from ..platform.messages import UploadResult
from ..models.client import ChatModel, ToolCall
from ..models.request import ChatRequest
from ..persona.profile import Persona
from ..persona.knowledge import PERSONA_KNOWLEDGE_TOOL, PersonaKnowledgeArguments, persona_knowledge
from ..platform.platform_tools import (MEMBER_INFO_TOOL, OPEN_FORWARD_TOOL, MemberInfoArguments,
                             OpenForwardArguments, PlatformCall, member_info, open_forward)
from .recall import RECALL_TOOL, RecallArguments, recall_chat
from ..trials.replay_images import RecordedImages
from ..trials.replay_web import RecordedWeb
from .scene_control import SCENE_CONTROL_TOOL, SceneControlArguments
from .schedule import SCHEDULE_TOOLS, execute_schedule
from ..storage.store import ImageAsset, Store, encode
from ..work.store import TaskStore
from ..work.tools import DELEGATE_TOOL, TASK_TOOL, execute_tasks
from ..tools.web_read import WEB_READ_TOOL, WebReadArguments, execute_web_read
from ..tools.web_search import WEB_SEARCH_TOOL, WebSearchArguments, execute_web_search

if TYPE_CHECKING:
    from .expression import ChatExpression
    from ..work.service import WorkTasks


class SayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1)
    end_turn: bool = Field(default=False, description="这是本轮最后一次表达，成功后本轮即可结束；还需查看工具结果或继续行动时保持 false。")
    reply_to: str | None = Field(default=None, description="需要引用时填本场景已有平台消息 ID；直接接话可省略。")
    mention: str | None = Field(default=None, pattern=r"^[0-9]+$",
        description="需要提醒特定对象时填实际 QQ；连续对话中对象清楚时可省略。")
    length: Literal["短", "正常", "长"] = Field(default="正常", description="本次表达的详略倾向；范围由实际请求决定，不代表固定字数。")


class ReactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    end_turn: bool = Field(default=False, description="这次表情完成本轮回应，成功后即可结束；还要继续处理时保持 false。")
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
    "name": "wait", "description": "短时等待群友补充消息，新消息可提前结束；受本轮剩余时限约束。"
        "后台任务由已有追问和结束事件唤醒，委托后结束本轮，不用此工具轮询任务。",
    "parameters": WaitArguments.model_json_schema(),
}}


def tool_catalog(*, platform: bool) -> list[dict]:
    say = SAY_TOOL if not platform else {"type": "function", "function": {
        **SAY_TOOL["function"], "description": "在当前场景表达；结果返回实际原文和平台发送状态。",
    }}
    catalog = [say, REACT_TOOL, WAIT_TOOL, RECALL_TOOL, WEB_SEARCH_TOOL, WEB_READ_TOOL, LOOK_TOOL,
            *SCHEDULE_TOOLS, PERSONA_KNOWLEDGE_TOOL, MEMORY_TOOL, DELEGATE_TOOL, TASK_TOOL,
            SEND_FILE_TOOL, OPEN_FORWARD_TOOL, MEMBER_INFO_TOOL, TRANSCRIBE_TOOL, SCENE_CONTROL_TOOL, TOOL_SEARCH]
    return [{**tool, "function": {**tool["function"],
             "parameters": model_schema(tool["function"]["parameters"])}} for tool in catalog]


def tool_result(value: object) -> str:
    """Compact model-visible JSON only; storage and external tool text are unchanged."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def tool_unavailable_reasons(config: LabConfig, persona: Persona, name: str) -> list[str]:
    reasons = []
    if persona.tools != "all" and name not in persona.tools:
        reasons.append("角色没有允许这个工具")
    if (name == "react" and not persona.stickers
            and not (config.learning is not None and config.learning.collect_stickers)):
        reasons.append("角色没有表情素材，本群也没有开启收集表情")
    if name == "web_read" and config.web_read is None:
        reasons.append("还没有开启读网页")
    if name == "web_search" and config.web_search is None:
        reasons.append("还没有开启搜索网页")
    if name == "memory" and config.memory is None:
        reasons.append("还没有设置记忆")
    if name == "look" and config.models.roles.vision is None:
        reasons.append("还没有设置看图模型")
    if name == "transcribe" and config.models.roles.asr is None:
        reasons.append("还没有设置语音转写模型")
    if name == "schedule" and not config.schedules.enabled:
        reasons.append("本群没有开启提醒")
    if name in {"delegate", "task"} and config.worker is None:
        reasons.append("还没有启用独立任务")
    if name == "delegate" and not config.tasks.enabled:
        reasons.append("本群没有开启任务")
    if name == "send_file":
        if config.worker is None:
            reasons.append("还没有启用独立任务")
        if config.onebot is None or config.onebot.upload_visible_root is None:
            reasons.append("还没有设置 NapCat 能看到的文件目录")
        if config.delivery != "onebot":
            reasons.append("模拟发送时不能发文件")
    if name == "persona_knowledge" and not persona.knowledge:
        reasons.append("角色没有资料文件")
    if name in {"open_forward", "member_info", "transcribe"} and config.delivery != "onebot":
        reasons.append("模拟发送时用不了")
    if name == "member_info" and not config.scene.startswith("group:"):
        reasons.append("只能在群里用")
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
    for index, tool in enumerate(allowed):
        function = tool["function"]
        if function["name"] == "say":
            parameters = SayArguments.model_json_schema()
            mode = "next_direct.md" if config.voice_mode == "direct" else "next_intent.md"
            parameters["properties"]["content"]["description"] = (PROMPTS / mode).read_text().strip()
            allowed[index] = {**tool, "function": {**function, "parameters": parameters}}
        elif function["name"] == "react":
            allowed[index] = {**tool, "function": {**function, "description": function["description"]
                              + " 当前角色情绪标签：" + encode(emotions)}}
    return allowed


class SceneTools:
    """Actual scene capabilities and dispatch; domain services own tool behavior."""

    def __init__(self, config: LabConfig, persona: Persona, store: Store, *,
                 expression: ChatExpression, request: ChatRequest, vision: ChatModel | None,
                 memory: MemoryService | None, tasks: WorkTasks | None, audio: AudioService | None,
                 upload_file: Callable[[str, str, str], Awaitable[UploadResult]] | None,
                 platform_call: PlatformCall | None, notify: Callable[[], None], now: Callable[[], float]):
        self.config, self.persona, self.store = config, persona, store
        self.expression, self.request, self.vision = expression, request, vision
        self.memory, self.tasks, self.audio = memory, tasks, audio
        self.upload_file, self.platform_call = upload_file, platform_call
        self.notify, self.now = notify, now
        self.scene_control: Callable[[SceneControlArguments], dict] | None = None
        self.replay_web = None if config.replay_web is None else RecordedWeb(config.replay_web)
        self.replay_images = (None if config.replay_images is None else
                              RecordedImages(config.replay_images, max_bytes=config.images.max_bytes))

    def set_external_tools(self, external_tools: list[ExternalTool]) -> list[dict]:
        """Replace the actual external capability set after service startup/closure."""
        config, persona = self.config, self.persona
        send_message, upload_file, platform_call = self.expression.send_message, self.upload_file, self.platform_call
        allowed = build_tools(config, persona, platform=send_message is not None)
        if self.scene_control is None:
            allowed = [tool for tool in allowed if tool["function"]["name"] != "scene_control"]
        # Plugin and MCP tools are always low-frequency and still need the role's permission.
        self.external = {tool.name: tool for tool in sorted(external_tools, key=lambda tool: tool.name)
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
        return allowed

    @property
    def tools(self) -> list[dict]:
        return self.core_tools + [tool for tool in self.deferred_tools
                                  if tool["function"]["name"] in self.discovered_tools]

    @property
    def tool_names(self) -> set[str]:
        return {tool["function"]["name"] for tool in self.tools}

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

    async def execute(self, turn_id: str, call: ToolCall,
                      wait_for_messages: Callable[[float], Awaitable[str]], *,
                      expression_style: str | None = None, direct: bool = False,
                      ) -> tuple[str, Expression | None, list[str] | None]:
        if call.name not in self.tool_names:
            raise ValueError(f"当前请求未开放工具：{call.name}")
        if call.name == "tool_search":
            arguments = ToolSearchArguments.model_validate(call.arguments)
            matched = search_tools(arguments.query, self.deferred_tools)
            names = [tool["function"]["name"] for tool in matched]
            discovered = sorted(set(self.store.load_discovered_tools(self.config.scene)) | set(names))
            return tool_result({"available_from": "next_model_request", "tools": [
                {"name": tool["function"]["name"], "description": tool["function"]["description"]}
                for tool in matched]}), None, discovered
        if call.name in self.external:
            tool = self.external[call.name]
            return await tool.call(self.config.scene, call.arguments), None, None
        if call.name == "say":
            arguments = SayArguments.model_validate(call.arguments)
            expression = await self.expression.express(turn_id, arguments,
                                                       expression_style=expression_style, direct=direct)
            return self.expression.context.render(expression), Expression(expression, end_turn=arguments.end_turn), None
        if call.name == "react":
            expression = self.expression.react(ReactArguments.model_validate(call.arguments))
            return self.expression.context.render(expression.message), expression, None
        if call.name == "scene_control":
            return tool_result(self.scene_control(SceneControlArguments.model_validate(call.arguments))), None, None
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
            return tool_result(await execute_tasks(self.tasks, self.config.scene, call.name, call.arguments)), None, None
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
                turn_id=turn_id, direct=direct,
            ), None, None
        arguments = WaitArguments.model_validate(call.arguments)
        return await wait_for_messages(arguments.seconds), None, None
