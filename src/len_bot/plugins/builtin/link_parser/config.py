from pydantic import BaseModel, ConfigDict, Field

class LinkParserConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled_platforms: list[str] = Field(default_factory=lambda: ['bilibili'])
    request_timeout_seconds: float = Field(default=15.0, gt=0)
