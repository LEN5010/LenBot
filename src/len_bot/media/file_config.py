from pydantic import BaseModel, ConfigDict, Field

MAX_FILE_BYTES = 50_000_000


class FileDeliveryConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    retention_seconds: int = Field(default=604800, ge=3600, le=2592000)
    max_file_bytes: int = Field(default=MAX_FILE_BYTES, ge=1, le=MAX_FILE_BYTES)
    daily_group_limit: int = Field(default=10, ge=0, le=10)


