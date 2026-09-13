"""Worker Gateway deployment configuration and its fixed reference registry.

The Gateway is the only component that holds a container runtime.  LenBot asks
it to run an execution and names an *image reference* and a *network policy
reference*; those names are resolved here, against the deployment's own
configuration.  A caller therefore cannot choose an image, a mount, a host
directory or an egress rule — it can only ask for a reference this deployment
has already built, and an unknown name is refused instead of being run with a
substituted meaning.

Only the offline policy exists in this version.  Network modes belong to the
later egress work and are refused here while they are absent, so "asked for
egress we do not have" can never be read as "ran without the egress it asked
for".
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class NetworkPolicy(BaseModel):
    """One egress rule this deployment has actually built."""
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    mode: str = Field(pattern=r'^(none)$', description='本版只实现离线；出网模式在后续阶段加入')


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
    max_output_chars: int = Field(default=12000, ge=100, le=100_000)
    max_artifacts: int = Field(default=1000, ge=1, le=10000,
                               description='一次执行登记产物条目上限；超出时只登记已列出的部分')
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
            raise KeyError(f'未登记的网络策略引用 {reference}；本版只提供离线执行')
        return policy
