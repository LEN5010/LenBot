from pydantic import BaseModel, ConfigDict, model_validator

from len_bot.execution.client import WorkerGatewayConfig
from len_bot.execution.workspace import WorkspaceConfig

# Input export, result reading and container cleanup happen outside the
# execution deadline, so the outer tool call gets this much more than the
# backend's own execution window.
CALL_MARGIN_SECONDS = 30.0


class WorkspacePluginConfig(BaseModel):
    """One formal execution backend: the local trial worker or the Gateway.

    Exactly one must be configured.  There is deliberately no runtime
    fallback between them — switching is an operator's root-config edit, so a
    gateway outage can never silently become a host ``docker run``.

    The two backends are declared as one exclusive group so the panel offers a
    single choice instead of two JSON boxes: a first configuration can then
    never submit both objects and be rejected for a mistake the form made.
    """
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True,
        json_schema_extra={
            'x-lenbot-exclusive': [{
                'fields': ['worker', 'gateway'],
                'title': '执行后端',
                'hint': '本机试用 worker 与隔离 gateway 只能选择一个；两者之间没有运行时回落，'
                        '切换由运营者改根配置完成。未选中的后端不会写入配置。'}]})
    worker: WorkspaceConfig | None = None
    gateway: WorkerGatewayConfig | None = None

    @model_validator(mode='after')
    def one_backend(self):
        if (self.worker is None) == (self.gateway is None):
            raise ValueError('workspace 需要且只能配置一个执行后端：本机试用 worker 或隔离 gateway')
        return self

    @property
    def execution_timeout_seconds(self) -> float:
        """The configured backend's own execution deadline.

        Read from whichever backend this configuration actually selected, so a
        gateway-only configuration never dereferences the absent worker.
        """
        if self.gateway is not None:
            return self.gateway.execution_timeout_seconds
        return self.worker.timeout_seconds

    @property
    def call_timeout_seconds(self) -> float:
        """The outer tool deadline: execution plus this backend's own overhead.

        The inner deadline covers the container run only.  The tool call must
        also cover input export, result reading and cleanup, so it is strictly
        larger than the execution deadline instead of racing it.  The gateway
        adds one request round trip on top, because its submit and poll are
        remote calls the local worker does not make.
        """
        overhead = CALL_MARGIN_SECONDS
        if self.gateway is not None:
            overhead += self.gateway.request_timeout_seconds
        return self.execution_timeout_seconds + overhead
