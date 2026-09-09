from pydantic import BaseModel, ConfigDict, Field


class SearchPluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    max_results: int = Field(ge=1, le=10)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
