"""Public, task-scoped execution request and artifact models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Observations and attachments share one admission count: the container's
# read-only input area is sized for this many files plus its manifest.
MAX_INPUT_ITEMS = 8


class RunPythonInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    script: str = Field(min_length=1, max_length=100_000)
    input_result_ids: list[str] = Field(default_factory=list, max_length=MAX_INPUT_ITEMS,
        title='文本资料', description='已保存资料（观察）的 result_id；正文作为文本输入导出')
    input_asset_ids: list[str] = Field(default_factory=list, max_length=MAX_INPUT_ITEMS,
        title='图片附件', description='本工作来源中已登记媒体资产的 asset_id；真实字节作为文件输入导出，'
                    '面板或发送动作不会因此发生')

    @model_validator(mode='after')
    def one_input_budget(self):
        total = len(set(self.input_result_ids)) + len(set(self.input_asset_ids))
        if total > MAX_INPUT_ITEMS:
            raise ValueError(f'一次执行的输入（资料与媒体资产合计）不能超过 {MAX_INPUT_ITEMS} 份')
        return self


class WorkspaceFileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str = Field(min_length=1, max_length=240)
    offset: int = Field(default=0, ge=0, description='文本字符偏移；不是字节偏移')
    limit: int = Field(default=12_000, ge=1, le=100_000)
    execution_id: str | None = Field(default=None, max_length=64,
        description='历史产物读取：指定某次执行的产物身份；不填只读当前工作区已确认快照')


class ListWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WorkspaceScope(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    scene_id: str
    requester_qq_uid: str | None = None
    system_subject: str | None = None
    job_id: str

    @model_validator(mode='after')
    def owner(self):
        if bool(self.requester_qq_uid) == bool(self.system_subject):
            raise ValueError('工作区必须恰好属于一名真实用户或一个类型化系统主体')
        return self

    @property
    def workspace_id(self) -> str:
        # These identifiers are already constrained by the job/event contracts;
        # preserve ownership without a content hash or model-selected owner.
        scene = self.scene_id.replace(':', '_')
        user = (self.requester_qq_uid or ('system_' + self.system_subject)).replace(':', '_')
        return f'{scene}__{user}__{self.job_id}'


class WorkspaceArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    path: str
    size_bytes: int = Field(ge=0)
    media_type: str = 'application/octet-stream'
    asset_id: str | None = None
    over_limit: bool = False
