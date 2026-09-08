"""Stage one normal work proposal or read its immutable current-group window."""
from __future__ import annotations

import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginPermission, PluginType
from len_bot.tools.results import ToolResult

from .config import GroupSummaryConfig
from .service import GroupSummaryService


class SummarizeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime = Field(description="含明确时区的ISO日期时间；范围含起点")
    end_at: AwareDatetime = Field(description="含明确时区的ISO日期时间；范围不含终点")
    focus: str = Field(description="本次要关注的事件或话题；空字符串表示按主要事件组织")
    evidence: list[str] = Field(min_length=1, description="本轮实际读过、明确要求总结的消息M引用")

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def timestamp_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("时间必须填写含时区的ISO字符串")
        return value

    @model_validator(mode="after")
    def ordered_range(self):
        if self.start_at >= self.end_at:
            raise ValueError("总结范围必须满足start_at < end_at")
        return self


class ReadWindowArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursor: str | None = Field(description="首次读取为null；之后原样复制该工作返回的next_cursor")


class GroupSummaryPlugin(BasePlugin):
    def __init__(self, *, config: GroupSummaryConfig, enabled: bool):
        super().__init__(PluginManifest(
            id="group_summary", name="当前群按需总结", version="1.0.0",
            description="按明确时间范围总结本群已保存的人类消息，复用原工作与回执链。",
            plugin_type=PluginType.TOOL, permissions=[PluginPermission.REGISTER_TOOL],
            timeout_seconds=config.tool_timeout_seconds,
            config=config.model_dump(), enabled=enabled,
            config_schema=GroupSummaryConfig.model_json_schema(),
            registered_tools=["summarize_group_chat", "read_group_chat_window"]))
        self.config = config
        self.service = None

    async def on_load(self, context: PluginContext) -> None:
        self.service = GroupSummaryService(context.event_store, self.config)
        context.register_tool(
            "summarize_group_chat", "暂存当前群的时间范围总结工作；finish_turn提交后才启动。",
            SummarizeArguments.model_json_schema(), self.summarize,
            kind="proposal", roles=("conversation",))
        context.register_tool(
            "read_group_chat_window", "分页读取当前总结工作固定范围和快照内的本群原话；不能指定其它群或自行改范围。",
            ReadWindowArguments.model_json_schema(), self.read_window,
            kind="read", roles=("work",))

    async def summarize(self, arguments: dict, call: PluginCallContext) -> ToolResult:
        values = SummarizeArguments.model_validate(arguments)
        if call.ledger is None or call.role != "conversation":
            raise ValueError("总结提案只在当前对话Ledger中暂存")
        if call.scene_id != call.ledger.context.refs.scene_id or call.episode_id != call.ledger.episode_id:
            raise ValueError("总结提案不属于当前对话")
        receipt = call.ledger.stage_group_summary(**values.model_dump())
        return ToolResult(content=json.dumps(receipt, ensure_ascii=False), coverage="staged_proposal", evidence_kind="model")

    async def read_window(self, arguments: dict, call: PluginCallContext) -> ToolResult:
        values = ReadWindowArguments.model_validate(arguments)
        return await self.service.read_window(values.cursor, call)
