"""The worker Gateway's wire contract, parsed once at each boundary.

LenBot assembles a request from facts it already holds: the work an execution
belongs to and its revision, the typed initiator, the workspace name, the
script the model wrote, the assets it is allowed to hand over, and the
absolute time it is willing to pay for.  No field here can name a mount, a
host directory, a container user, a Docker parameter or an image — those come
from the Gateway's own deployment configuration, so code the model can write
still cannot choose where or as whom it runs.

Technical state is not business outcome.  ``EXITED`` says a process ended,
never that the work's answer is right; the work's own completed/partial/failed
result stays in the work ledger and is never derived from anything here.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from len_bot.events.models import Initiator

# A worker type is a name the Gateway resolves in its own registry.  The
# pattern only keeps the value a plain identifier; whether the name exists is
# decided by the registry, never by a substituted default.
WORKER_TYPE_PATTERN = r'^[a-z][a-z0-9_]{0,31}$'

# A wire-level ceiling, not the deployment's own output limit.  The Gateway
# collects a run's streams under its configured bound and marks what it cut;
# this number only keeps an oversized or malformed payload from being stored
# at all, so the journal never grows an unbounded text column.
MAX_OUTPUT_CHARS = 20_000


class ExecutionState(StrEnum):
    """Where an external execution actually is.

    The order is the plan's: accepted → starting → running →
    exited/failed/cancel_requested → termination_confirmed or
    termination_unconfirmed.  ``cancel_requested`` is not a stop: it records
    that a stop was asked for, and the termination outcome follows it.
    """
    ACCEPTED = 'accepted'
    STARTING = 'starting'
    RUNNING = 'running'
    EXITED = 'exited'
    FAILED = 'failed'
    CANCEL_REQUESTED = 'cancel_requested'
    TERMINATION_CONFIRMED = 'termination_confirmed'
    TERMINATION_UNCONFIRMED = 'termination_unconfirmed'


# A run that stops here will not change on its own.  ``exited``/``failed`` are
# the run's own end; the termination states are the answer to "is the container
# really gone" and replace the end state whenever a stop had to be carried out.
TERMINAL_STATES = frozenset({
    ExecutionState.EXITED, ExecutionState.FAILED,
    ExecutionState.TERMINATION_CONFIRMED, ExecutionState.TERMINATION_UNCONFIRMED,
})


class TerminationReport(BaseModel):
    """How an execution's container ended, as far as it could be established.

    ``unconfirmed`` is a real answer: the Gateway could not establish that the
    container is stopped and removed, and it is never written as a success.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    status: str = Field(pattern=r'^(confirmed_absent|confirmed_stopped|unconfirmed)$')
    detail: str = Field(default='', max_length=2000)
    container_name: str | None = Field(default=None, max_length=200)


class ExecutionEvent(BaseModel):
    """One ordered fact about an execution, read back by sequence.

    A reader keeps the highest sequence it has adopted, so re-reading the same
    window after a reconnect cannot apply an event twice.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=64)
    at: float
    detail: str = Field(default='', max_length=2000)


class ExecutionRecord(BaseModel):
    """One execution as the Gateway reports it after any call."""
    model_config = ConfigDict(extra='forbid', strict=True)
    execution_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    scene_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    job_revision: int = Field(ge=1)
    workspace_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    worker_type: str = Field(pattern=WORKER_TYPE_PATTERN)
    image_ref: str = Field(min_length=1, max_length=64)
    network_policy: str = Field(min_length=1, max_length=64)
    state: ExecutionState
    accepted_at: float
    deadline_at: float
    started_at: float | None = None
    ended_at: float | None = None
    returncode: int | None = None
    error: str | None = Field(default=None, max_length=2000)
    termination: TerminationReport | None = None
    stdout: str = Field(default='', max_length=MAX_OUTPUT_CHARS)
    stderr: str = Field(default='', max_length=MAX_OUTPUT_CHARS)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    last_sequence: int = Field(ge=0)


class ExecutionRequest(BaseModel):
    """What LenBot asks the Gateway to run.

    This is the only shape the Gateway accepts.  The image and the network
    policy are *references* the Gateway resolves against its own deployment
    configuration, so an unknown reference is refused rather than run with a
    substituted meaning; the same rule keeps a caller from asking for egress
    the deployment has not built.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    execution_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$',
                              description='宿主组装的执行身份；同一 ID 重复提交不重复启动')
    scene_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    job_revision: int = Field(ge=1, description='工作修订；一次修订就是一个执行代次')
    workspace_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    initiator: Initiator
    worker_type: str = Field(pattern=WORKER_TYPE_PATTERN,
                            description='本版只有 python；未登记的 worker 类型不被执行')
    script: str = Field(min_length=1, max_length=100_000)
    image_ref: str = Field(min_length=1, max_length=64,
                           description='固定镜像配置引用，由 Gateway 解析为实际镜像')
    network_policy: str = Field(min_length=1, max_length=64,
                               description='网络策略引用，由 Gateway 解析为实际出口规则')
    input_assets: list[str] = Field(default_factory=list, max_length=8)
    deadline_seconds: float = Field(gt=0, le=3600,
                                    description='宿主愿意为本次执行支付的绝对时间；从被接受时起算')


def is_terminal(state: ExecutionState) -> bool:
    return state in TERMINAL_STATES
