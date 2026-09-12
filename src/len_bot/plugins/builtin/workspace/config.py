from pydantic import BaseModel, ConfigDict
from len_bot.execution.workspace import WorkspaceConfig


class WorkspacePluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    worker: WorkspaceConfig
