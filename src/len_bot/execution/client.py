"""The host's client for the worker Gateway's narrow interface.

Only this module knows the transport.  Everything above it hands over an
``ExecutionRequest`` and reads back protocol types, so a caller cannot
accidentally encode a mount, a host path or a Docker flag into a request: the
request type does not carry them.

Two failures are deliberately different.  A refusal is the Gateway's answer —
the request was seen and rejected, and nothing runs.  An unavailability is not
an answer: the execution may exist and may still be running, so the only
correct next step is to query the same execution id.  Submitting a fresh one
after a timeout would be exactly the second container the journal prevents.
"""
from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from len_bot.execution.protocol import (
    ExecutionEvent, ExecutionRecord, ExecutionRequest, ExecutionState, TerminationReport,
)

# References are names the Gateway resolves against its own deployment
# configuration.  Keeping them identifiers here is what stops a caller from
# choosing an image or an egress rule by writing a value.
REFERENCE_PATTERN = r'^[a-z][a-z0-9_-]{0,63}$'


class WorkerGatewayConfig(BaseModel):
    """Where the Gateway is and which fixed references this deployment uses."""
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    base_url: str = Field(pattern=r'^https?://[^\s]+$', title='网关地址',
        description='网关 HTTP 地址；容器运行时不在 LenBot 进程里')
    token: str = Field(min_length=16, title='服务 token', description='服务间认证密钥；不交给执行容器')
    image_ref: str = Field(pattern=REFERENCE_PATTERN, title='镜像引用名',
        description='网关自己 images 表里的引用名，不是镜像名或 Docker 参数')
    network_policy: str = Field(pattern=REFERENCE_PATTERN, title='网络策略引用名',
        description='网关自己 network_policies 表里的引用名；本版只实现离线 none')
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300, title='单次 HTTP 超时（秒）',
        description='提交与查询各自的 HTTP 超时')
    execution_timeout_seconds: float = Field(default=30.0, gt=0, le=3600, title='单次执行期限（秒）',
        description='单次执行的绝对期限；实际提交时不超过原工作剩余期限')
    poll_interval_seconds: float = Field(default=1.0, gt=0, le=30, title='终态查询间隔（秒）',
        description='查询同一执行 ID 的间隔；取消或冲突时仍只查这个 ID')


class GatewayRefused(RuntimeError):
    """The Gateway answered and refused before starting this request."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f'Gateway 拒绝（HTTP {status_code}）：{detail}')
        self.status_code = status_code
        self.detail = detail


class GatewayConflict(RuntimeError):
    """The identity already exists or the workspace is occupied; query that id."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f'Gateway 身份冲突（HTTP {status_code}）：{detail}')
        self.status_code = status_code
        self.detail = detail


class GatewayResultUnknown(RuntimeError):
    """The answer cannot prove whether an execution exists; do not start a new id."""

    def __init__(self, detail: str):
        super().__init__(f'Gateway 结果不能确认：{detail}')
        self.detail = detail


class GatewayUnavailable(RuntimeError):
    """The Gateway could not be reached or did not answer in time.

    The execution's outcome is unknown.  Query the same execution id; do not
    submit a new one.
    """


class SubmittedExecution(BaseModel):
    """A submission's answer: the stored record and whether it was new."""
    model_config = ConfigDict(extra='forbid', strict=True)
    record: ExecutionRecord
    accepted: bool = Field(description='true 表示本次真的新登记；false 表示同一 ID 已存在，未重复启动')


class CancellationOutcome(BaseModel):
    """The answer to a cancel request: the record and whether a stop was carried out.

    ``requested`` false with a terminal record means the run had already ended
    on its own and nothing was stopped.  A ``termination`` is present only when
    a stop was attempted, and its status may still be ``unconfirmed``.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    record: ExecutionRecord
    requested: bool
    termination: TerminationReport | None = None


class WorkerGatewayClient:
    def __init__(self, config: WorkerGatewayConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds,
            trust_env=False, follow_redirects=False,
            headers={'Authorization': f'Bearer {config.token}'})

    async def close(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, *, json=None, params=None):
        try:
            response = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as error:
            raise GatewayUnavailable(f'Gateway 未响应：{error}') from None
        if response.status_code == 409:
            raise GatewayConflict(response.status_code, _detail_of(response))
        if 400 <= response.status_code < 500:
            raise GatewayRefused(response.status_code, _detail_of(response))
        if response.status_code >= 500:
            raise GatewayResultUnknown(f'HTTP {response.status_code}：{_detail_of(response)}')
        try:
            return response.json()
        except ValueError as error:
            raise GatewayResultUnknown(f'响应不是合法 JSON：{error}') from None

    @staticmethod
    def _record(payload) -> ExecutionRecord:
        if not isinstance(payload, dict):
            raise GatewayResultUnknown('执行记录不是对象')
        data = dict(payload)
        state = data.get('state')
        if isinstance(state, str):
            try:
                data['state'] = ExecutionState(state)
            except ValueError as error:
                raise GatewayResultUnknown(f'无法解析执行状态：{error}') from None
        try:
            return ExecutionRecord.model_validate(data)
        except Exception as error:
            raise GatewayResultUnknown(f'执行记录无法解析：{error}') from None

    async def submit(self, request: ExecutionRequest) -> SubmittedExecution:
        """Register one execution; the same id is never started twice."""
        payload = await self._request('POST', '/v1/executions', json=request.model_dump(mode='json'))
        if not isinstance(payload, dict):
            raise GatewayResultUnknown('提交响应不是对象')
        accepted = payload.get('accepted')
        if not isinstance(accepted, bool):
            # A missing flag must not be read as "already existed": that answer
            # decides whether a run was started, so its absence is unknown.
            raise GatewayResultUnknown('提交响应缺少明确的 accepted 布尔标记')
        return SubmittedExecution(record=self._record(payload.get('record')), accepted=accepted)

    async def get(self, execution_id: str) -> ExecutionRecord:
        return self._record(await self._request('GET', f'/v1/executions/{execution_id}'))

    async def cancel(self, execution_id: str, reason: str) -> CancellationOutcome:
        payload = await self._request('POST', f'/v1/executions/{execution_id}/cancel',
                                      json={'reason': reason})
        if not isinstance(payload, dict):
            raise GatewayResultUnknown('取消响应不是对象')
        requested = payload.get('requested')
        if not isinstance(requested, bool):
            raise GatewayResultUnknown('取消响应缺少明确的 requested 布尔标记')
        termination = payload.get('termination')
        return CancellationOutcome(
            record=self._record(payload.get('record')),
            requested=requested,
            termination=None if termination is None else TerminationReport.model_validate(termination))

    async def events(self, execution_id: str, after: int = 0) -> list[ExecutionEvent]:
        """Facts with a sequence above ``after``; re-reading never repeats one."""
        payload = await self._request('GET', f'/v1/executions/{execution_id}/events',
                                      params={'after': after})
        return [ExecutionEvent.model_validate(item) for item in payload['events']]

    async def artifacts(self, execution_id: str) -> dict:
        return await self._request('GET', f'/v1/executions/{execution_id}/artifacts')

    async def artifact_chunks(self, artifact_id: str, *, offset: int = 0, limit: int | None = None):
        """One registered file's bytes as they arrive, from a byte position.

        The Gateway seeks on its own stored copy before reading, so a caller
        that wants a page of a large file never pulls the whole file through
        itself first.
        """
        params = {'offset': offset} if offset else None
        if limit is not None:
            params = {**(params or {}), 'limit': limit}
        try:
            async with self._client.stream('GET', f'/v1/artifacts/{artifact_id}',
                                           params=params) as response:
                if response.status_code >= 500:
                    await response.aread()
                    raise GatewayResultUnknown(
                        f'HTTP {response.status_code}：{_detail_of(response)}')
                if response.status_code >= 400:
                    await response.aread()
                    raise GatewayRefused(response.status_code, _detail_of(response))
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as error:
            raise GatewayUnavailable(f'Gateway 未响应：{error}') from None


def _detail_of(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or 'no detail'
    detail = payload.get('detail') if isinstance(payload, dict) else None
    return str(detail)[:500] if detail else str(payload)[:500]
