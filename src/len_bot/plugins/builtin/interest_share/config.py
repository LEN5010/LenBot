from pydantic import BaseModel, ConfigDict, Field, model_validator


class InterestShareConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    max_steps: int = Field(ge=1, le=8, description='社会表达调用上限，使用现有 conversation 模型')
    context_tokens: int = Field(ge=1024)
    output_tokens: int = Field(ge=128)

    @model_validator(mode='after')
    def capacity(self):
        if self.output_tokens >= self.context_tokens:
            raise ValueError('输出预留必须小于上下文容量')
        return self


class InterestShareScene(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    topics: list[str] = Field(max_length=20, description='只选这些公共主题；空列表允许全部有效主题，仍由 Agent 判断本群相关性')
    daily_limit: int = Field(ge=0, le=100, description='本群每日主动分享上限；0 不发送，unknown 保留占用')
    cooldown_seconds: int = Field(ge=60, description='本群两次主动分享的最短间隔')


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task_id: str
    slot: int
    interest_id: str
    revision: int = Field(ge=1)
