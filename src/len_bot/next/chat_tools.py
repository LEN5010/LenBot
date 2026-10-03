"""Core chat tool schemas and availability, shared by runtime and configuration callers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .audio import TRANSCRIBE_TOOL
from .config import LabConfig
from .discovery import DEFERRED_NAMES, TOOL_SEARCH
from .file_delivery import SEND_FILE_TOOL
from .images import LOOK_TOOL
from .memory import MEMORY_TOOL
from .persona import Persona
from .persona_knowledge import PERSONA_KNOWLEDGE_TOOL
from .platform_tools import MEMBER_INFO_TOOL, OPEN_FORWARD_TOOL
from .recall import RECALL_TOOL
from .scene_control import SCENE_CONTROL_TOOL
from .schedule import SCHEDULE_TOOLS
from .store import encode
from .tasks_tools import DELEGATE_TOOL, TASK_TOOL
from .web_read import WEB_READ_TOOL
from .web_search import WEB_SEARCH_TOOL


class SayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1)
    reply_to: str | None = Field(default=None, description="需要引用时填本场景已有平台消息 ID；直接接话可省略。")
    mention: str | None = Field(default=None, pattern=r"^[0-9]+$",
        description="需要提醒特定对象时填实际 QQ；连续对话中对象清楚时可省略。")
    length: Literal["短", "正常", "长"] = Field(default="正常", description="本次表达的详略意向，所需事实仍完整保留。")


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
    return [say, REACT_TOOL, WAIT_TOOL, RECALL_TOOL, WEB_SEARCH_TOOL, WEB_READ_TOOL, LOOK_TOOL,
            *SCHEDULE_TOOLS, PERSONA_KNOWLEDGE_TOOL, MEMORY_TOOL, DELEGATE_TOOL, TASK_TOOL,
            SEND_FILE_TOOL, OPEN_FORWARD_TOOL, MEMBER_INFO_TOOL, TRANSCRIBE_TOOL, SCENE_CONTROL_TOOL, TOOL_SEARCH]


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
    return [{**tool, "function": {**tool["function"], "description": tool["function"]["description"]
                 + " 当前角色情绪标签：" + encode(emotions)}}
            if tool["function"]["name"] == "react" else tool for tool in allowed]
