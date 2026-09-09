from pydantic import BaseModel, ConfigDict, Field


class BilibiliPluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sessdata: str = Field(json_schema_extra={'writeOnly': True})
    bili_jct: str = Field(json_schema_extra={'writeOnly': True})
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
