from pydantic import BaseModel, ConfigDict, model_validator

from len_bot.execution.client import WorkerGatewayConfig
from len_bot.execution.workspace import WorkspaceConfig


class WorkspacePluginConfig(BaseModel):
    """One formal execution backend: the local trial worker or the Gateway.

    Exactly one must be configured.  There is deliberately no runtime
    fallback between them — switching is an operator's root-config edit, so a
    gateway outage can never silently become a host ``docker run``.
    """
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    worker: WorkspaceConfig | None = None
    gateway: WorkerGatewayConfig | None = None

    @model_validator(mode='after')
    def one_backend(self):
        if (self.worker is None) == (self.gateway is None):
            raise ValueError('workspace 需要且只能配置一个执行后端：本机试用 worker 或隔离 gateway')
        return self
