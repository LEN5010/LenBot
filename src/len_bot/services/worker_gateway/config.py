"""Worker Gateway deployment configuration and its fixed reference registry.

The Gateway is the only component that holds a container runtime.  LenBot asks
it to run an execution and names an *image reference* and a *network policy
reference*; those names are resolved here, against the deployment's own
configuration.  A caller therefore cannot choose an image, a mount, a host
directory or an egress rule — it can only ask for a reference this deployment
has already built, and an unknown name is refused instead of being run with a
substituted meaning.

Offline (``none``) is the policy every deployment has.  A proxy policy is a
second, explicit deployment artifact: it carries the destination classes the
worker's egress proxy forwards, and without one there is no code path that
starts a networked worker — "asked for egress we do not have" is refused here
rather than read as "ran without the egress it asked for".
"""
from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.services.worker_gateway.egress_policy import (
    DEFAULT_PORTS, HOST_CLASSES, EgressRules,
)

# A reference is a name, never a value.  Keeping the pattern here means both
# sides of the wire agree on what a reference may look like without either
# side accepting an image name or a Docker flag in that position.
REFERENCE_PATTERN = r'^[a-z][a-z0-9_-]{0,63}$'


class WorkerImage(BaseModel):
    """One worker this Gateway is allowed to run, under its reference name.

    The entry declares which worker type it implements and the image it runs
    that type in, so a request's ``worker_type`` and ``image_ref`` have to
    agree with something the deployment actually built.  A worker type nobody
    built is refused rather than run as whatever image happened to be named.
    """
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    worker_type: str = Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')
    image: str = Field(min_length=1, max_length=200)
    container_user: str = Field(default='65532:65532', pattern=r'^[0-9]+:[0-9]+$')
    memory: str = Field(default='512m', pattern=r'^[0-9]+[kmgKMG]?$')
    cpus: str = Field(default='1.0', pattern=r'^[0-9]+(\.[0-9]+)?$')
    pids_limit: int = Field(default=128, ge=1, le=4096)
    browser_seccomp_profile: str | None = Field(default=None,
        description='浏览器专用 seccomp 文件绝对路径，允许 Chromium 用户命名空间；不能填 unconfined')

    @model_validator(mode='after')
    def browser_sandbox(self):
        if self.worker_type == 'media' and self.container_user.split(':')[0] == '0':
            raise ValueError('媒体容器必须使用非 root 用户')
        if self.worker_type == 'browser':
            if self.container_user.split(':')[0] == '0':
                raise ValueError('浏览器容器必须使用非 root 用户')
            if not self.browser_seccomp_profile or not Path(self.browser_seccomp_profile).is_absolute():
                raise ValueError('浏览器镜像需要独立的绝对路径 seccomp 文件')
        elif self.browser_seccomp_profile is not None:
            raise ValueError('仅浏览器镜像可声明 browser_seccomp_profile')
        return self


class NetworkPolicy(BaseModel):
    """One egress rule this deployment has actually built.

    ``none`` is the offline worker: no network namespace of its own beyond the
    runtime's, and no route anywhere.

    ``proxy`` attaches the worker to an internal network whose only reachable
    address is this Gateway's egress proxy, and carries the destination
    classes that proxy forwards.  The allow lists are host names, never
    addresses or ports, and a list with nothing in it is refused: a policy
    that forwards nothing is not a policy to run networked work under, and
    saying so here is clearer than a run that fails at its first request.
    """
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    mode: str = Field(pattern=r'^(none|proxy)$',
        description='none 为离线；proxy 经网关出口代理转发，需填 hosts 与网络地址')
    network: str = Field(default='', max_length=64, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$',
        description='容器加入的内部网络名；只填写名称，由网关运行时按该名字解析')
    proxy_address: str = Field(default='', max_length=64,
        description='worker 所在内部网络里出口代理的地址，例如 172.31.9.2')
    proxy_port: int = Field(default=8799, ge=1, le=65535)
    deny_hosts: tuple[str, ...] = Field(default=(), max_length=128,
        description='无论属于哪个类别都不转发的目的主机；控制网段地址在此列出')
    hosts: dict[str, tuple[str, ...]] = Field(default_factory=dict,
        description='按类别列出的目的主机：business 为业务域，resource 为其资源域')
    ports: tuple[int, ...] = Field(default=DEFAULT_PORTS, min_length=1, max_length=8)
    max_upload_bytes: int = Field(default=2_000_000, ge=1, le=100_000_000)
    max_download_bytes: int = Field(default=20_000_000, ge=1, le=1_000_000_000)
    max_total_bytes: int = Field(default=50_000_000, ge=1, le=1_000_000_000)
    allow_websockets: bool = Field(default=False)
    deployment_verified: bool = Field(default=False,
        description='运营者确认 worker 网络只能到达本代理端口之后才为 true；未核验时拒绝联网入场')
    log_path: str = Field(default='', description='出口记录文件；留空表示不落盘，由进程日志承担')

    @field_validator('hosts', mode='before')
    @classmethod
    def known_classes(cls, value):
        """The class table as it comes out of a hand-written JSON file.

        Strict mode would refuse a JSON array where the field is a tuple, and
        an operator writing gateway JSON is writing arrays; the value is
        normalized here rather than loosening the field's type.
        """
        if value is None:
            return {}
        if not isinstance(value, dict) or set(value) - set(HOST_CLASSES):
            raise ValueError(f'hosts 的类别只能是 {"/".join(HOST_CLASSES)}')
        return {name: tuple(entries or ()) for name, entries in value.items()}

    @field_validator('deny_hosts', 'ports', mode='before')
    @classmethod
    def lists(cls, value):
        return tuple(value or ())

    @field_validator('ports')
    @classmethod
    def port_range(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError('端口必须在 1—65535 之间')
        if len(set(value)) != len(value):
            raise ValueError('端口不能重复')
        return value

    @model_validator(mode='after')
    def proxy_fields(self):
        if self.mode == 'none':
            # An offline policy keeps no egress rule to misread: the fields
            # that only mean something for a networked worker stay empty.
            if self.hosts or self.network or self.proxy_address:
                raise ValueError('离线策略不填写转发类别与网络地址；出网必须另建一条 proxy 策略')
            return self
        if not self.network or not self.proxy_address:
            raise ValueError('proxy 策略必须填写内部网络名与出口代理地址')
        if self.network.lower() in {'host', 'bridge', 'none', 'default'}:
            raise ValueError('proxy 策略不能使用 host/bridge/none/default；必须是本部署自建的内部网络名')
        try:
            address = ipaddress.ip_address(self.proxy_address)
        except ValueError as error:
            raise ValueError(f'出口代理地址必须是数字地址：{error}') from None
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise ValueError('出口代理地址必须是内部（私有）地址，不能是公网地址')
        if not self.hosts or not any(self.hosts.get(name) for name in HOST_CLASSES):
            raise ValueError('proxy 策略必须至少列出一个业务域或资源域，否则没有任何目标会被转发')
        return self

    def rules(self) -> EgressRules:
        """The rule set the proxy and the copied control file both read."""
        return EgressRules.from_document({
            'hosts': {name: list(self.hosts.get(name, ())) for name in HOST_CLASSES},
            'deny_hosts': list(self.deny_hosts), 'ports': list(self.ports),
            'budgets': {'request_bytes': self.max_upload_bytes,
                        'response_bytes': self.max_download_bytes,
                        'total_bytes': self.max_total_bytes},
            'websockets': self.allow_websockets})


class GatewayConfig(BaseModel):
    """How this Gateway runs, and where its own journal and volumes live."""
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    host: str = '127.0.0.1'
    port: int = Field(default=8790, ge=1, le=65535)
    token: str = Field(min_length=16, description='服务间认证密钥；不交给执行容器')
    database_path: str = Field(min_length=1, description='网关自己的执行日志 SQLite 路径')
    workspaces_root: str = Field(min_length=1, description='工作卷根目录；只由网关挂载')
    runtime_path: str = Field(default='/usr/bin/docker')
    images: dict[str, WorkerImage] = Field(min_length=1)
    network_policies: dict[str, NetworkPolicy] = Field(min_length=1)
    egress_bind_host: str = Field(default='0.0.0.0', min_length=1, max_length=64,
        description='出口代理监听的地址；worker 只应能从内部网络访问到它')
    max_output_chars: int = Field(default=12000, ge=100, le=100_000)
    max_artifacts: int = Field(default=1000, ge=1, le=10000,
                               description='一次执行登记产物条目上限；超出时只登记已列出的部分')
    max_workspace_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024,
                                     description='单次执行工作目录字节上限；超出时停止并如实标记')
    max_workspace_files: int = Field(default=2000, ge=1, le=100_000,
                                     description='单次执行工作目录普通文件数上限')
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    start_confirm_attempts: int = Field(default=20, ge=1, le=200)
    start_confirm_interval_seconds: float = Field(default=0.25, gt=0, le=5)
    sweep_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    max_concurrent: int = Field(default=2, ge=1, le=64,
                               description='同时存在的未终结执行上限；超出时明确拒绝而不排队')

    @field_validator('runtime_path')
    @classmethod
    def container_runtime_only(cls, value: str) -> str:
        # Reuse the same rule the host worker already enforces, so a
        # deployment cannot point this at a shell or a wrapper script.
        if Path(value).name not in {'docker', 'podman'}:
            raise ValueError('runtime_path must point to docker or podman')
        return value

    @field_validator('images', 'network_policies')
    @classmethod
    def reference_names(cls, value: dict) -> dict:
        for name in value:
            if not re.fullmatch(REFERENCE_PATTERN, name):
                raise ValueError(f'{name} 不是合法的引用名')
        return value

    @model_validator(mode='after')
    def unique_proxy_ports(self):
        ports = [policy.proxy_port for policy in self.network_policies.values()
                 if policy.mode == 'proxy']
        if len(ports) != len(set(ports)):
            raise ValueError('多条 proxy 策略不能共用同一代理端口')
        return self

    def worker_for(self, reference: str, worker_type: str) -> WorkerImage:
        """The registered worker a request names, or a refusal naming why.

        Both halves are checked together: the reference must exist *and* the
        worker type it declares must be the one asked for.  Otherwise a request
        could ask for a browser worker and be handed the Python image under a
        name that merely exists.
        """
        worker = self.images.get(reference)
        if worker is None:
            raise KeyError(f'未登记的 worker 引用 {reference}；本网关只在部署已构建的固定 worker 中运行')
        if worker.worker_type != worker_type:
            raise KeyError(
                f'worker 引用 {reference} 实现的是 {worker.worker_type}，本次请求的是 {worker_type}')
        return worker

    def policy_for(self, reference: str) -> NetworkPolicy:
        policy = self.network_policies.get(reference)
        if policy is None:
            raise KeyError(f'未登记的网络策略引用 {reference}；本网关只在部署已构建的策略中运行')
        return policy

    def proxy_policies(self) -> dict[str, NetworkPolicy]:
        """Every policy whose mode actually forwards through the egress proxy."""
        return {name: policy for name, policy in self.network_policies.items()
                if policy.mode == 'proxy'}
