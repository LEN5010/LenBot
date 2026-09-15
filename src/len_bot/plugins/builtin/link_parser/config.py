from pydantic import BaseModel, ConfigDict, Field

class LinkParserConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True,
        json_schema_extra={'x-lenbot-list-choices': {'enabled_platforms': ['bilibili']}})
    enabled_platforms: list[str] = Field(default_factory=lambda: ['bilibili'],
        title='解析哪些平台的链接', description='当前只有 B 站；空列表表示不解析任何链接')
    request_timeout_seconds: float = Field(default=15.0, gt=0)
