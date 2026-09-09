"""Operator parameters for summaries using the existing work budget."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GroupSummaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_messages: int = Field(ge=1, description="每页最多读取的已保存人类消息数")
    page_chars: int = Field(ge=1, description="每份资料的初始展示字符数，仍受完整工具组预算约束")
    tool_timeout_seconds: float = Field(gt=0, description="本地范围查询或提案的工具执行时限")
    output_instructions: str = Field(description="总结的组织与表达要求")
    work_budget: Literal["runtime"] = Field(description="复用当前Runtime工作预算，不配置独立模型或额外调用")
    render_font_path: str = Field(min_length=1,description='报告字体路径；相对路径以本插件目录为基准')
