"""Host-owned workspace admission, input export and artifact checks."""
from __future__ import annotations

import asyncio
import codecs
import json
import mimetypes
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from len_bot.events.models import Initiator
from len_bot.execution.client import (
    GatewayConflict, GatewayRefused, GatewayResultUnknown, GatewayUnavailable,
)
from len_bot.execution.models import RunPythonInput, WorkspaceArtifact, WorkspaceFileInput, WorkspaceScope
from len_bot.execution.protocol import ExecutionInputFile, ExecutionRequest, ExecutionState, is_terminal
from len_bot.execution.workspace import (
    FileRequest, RunPythonRequest, WorkspaceCancelled, WorkspaceRequest, WorkspaceWorker,
    _validate_relative_path, park_termination,
)


class WorkspaceService:
    def __init__(self, worker: WorkspaceWorker, event_store, plugin_id: str):
        self.worker = worker
        self.event_store = event_store
        self.plugin_id = plugin_id

    async def scope_for(self, call, *, allow_terminal: bool = False) -> WorkspaceScope:
        if call.role != 'work' or not call.job_id or not call.requester_qq_uid:
            raise ValueError('工作空间只允许在已有 work 工作中使用')
        job = await self.event_store.get_job(call.job_id, call.scene_id)
        if not job or job['requester_qq_uid'] != call.requester_qq_uid:
            raise ValueError('工作空间归属与当前工作不一致')
        origin = job.get('plugin_origin') or {}
        # Information work has no plugin origin; it is still a valid owner of
        # the scoped workspace. Specialized work must belong to this plugin.
        if origin and origin.get('plugin_id') != self.plugin_id:
            raise ValueError('工作空间不能读取其他插件工作的目录')
        allowed = {'pending', 'claimed', 'processing', 'result_ready'}
        if allow_terminal:
            allowed |= {'completed', 'failed', 'review_required', 'cancelled', 'delivery_unknown', 'shadow_observed'}
        if job['status'] not in allowed:
            raise ValueError('当前工作已结束，不能继续使用其执行目录')
        return WorkspaceScope(scene_id=call.scene_id, requester_qq_uid=call.requester_qq_uid, job_id=call.job_id)

    async def _export_inputs(self, scope: WorkspaceScope, result_ids: list[str], scene_id: str) -> list[dict]:
        if len(result_ids) > 8:
            raise ValueError('一次执行最多导出 8 份已取得资料')
        directory = self.worker.control_directory(scope.workspace_id) / 'input'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = await self.event_store.get_job(scope.job_id, scene_id)
        allowed_results = set(job.get('result_ids', ())) if job else set()
        manifest = []
        for index, result_id in enumerate(dict.fromkeys(result_ids), 1):
            if result_id not in allowed_results:
                raise ValueError(f'资料 {result_id} 尚未登记为当前工作的资料')
            observation = await self.event_store.read_tool_observation(result_id, [scene_id])
            if observation is None:
                raise ValueError(f'资料 {result_id} 不属于当前场景或已不存在')
            path = directory / f'result_{index}.txt'
            self.worker._write_control(path, observation.content)
            manifest.append({'result_id': result_id, 'path': f'/lenbot-control/input/result_{index}.txt',
                'coverage': observation.coverage, 'status': observation.status,
                'sources': [source.model_dump(mode='json') for source in observation.sources]})
        self.worker._write_control(self.worker.control_directory(scope.workspace_id) / 'manifest.json',
            json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

    def _artifacts(self, scope: WorkspaceScope) -> list[WorkspaceArtifact]:
        directory = self.worker.directory(scope.workspace_id)
        artifacts = []
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.name == '.lenbot_task.py':
                continue
            relative = str(path.relative_to(directory))
            if relative.startswith('input/'):
                continue
            try:
                info = self.worker.stat_file(FileRequest(workspace_id=scope.workspace_id, path=relative))
            except (FileNotFoundError, ValueError):
                continue
            size = info.st_size
            artifacts.append(WorkspaceArtifact(path=relative, size_bytes=size,
                media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                over_limit=size > self.worker.config.max_artifact_bytes))
        return artifacts[:self.worker.config.max_artifact_files]

    async def run_python(self, call, request: RunPythonInput) -> dict:
        scope = await self.scope_for(call)
        async with self.worker.run_lock(scope.workspace_id):
            self.worker.ensure_workspace_available(scope.workspace_id)
            manifest = await self._export_inputs(scope, request.input_result_ids, call.scene_id)
            result = await self.worker._run_python_locked(RunPythonRequest(workspace_id=scope.workspace_id, script=request.script))
            artifacts = self._artifacts(scope)
        result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'input_manifest': manifest, 'artifacts': [item.model_dump(mode='json') for item in artifacts],
            'artifacts_truncated': len(artifacts) >= self.worker.config.max_artifact_files}
        return result

    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        result = self.worker.list_files(WorkspaceRequest(workspace_id=scope.workspace_id))
        result.update({'scene_id': scope.scene_id, 'job_id': scope.job_id})
        return result

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        self.worker.ensure_workspace_available(scope.workspace_id)
        path = self.worker.file_path(scope.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('工作空间文件不存在或不是普通文件')
        content, next_offset = self.worker.read_text_range(
            FileRequest(workspace_id=scope.workspace_id, path=request.path), request.offset, request.limit)
        return {'scene_id': scope.scene_id, 'job_id': scope.job_id, 'path': request.path,
            'content': content, 'offset': request.offset, 'next_offset': next_offset,
            'truncated': next_offset is not None}

    async def export_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        self.worker.ensure_workspace_available(scope.workspace_id)
        path = self.worker.file_path(scope.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('工作空间文件不存在或不是普通文件')
        size = self.worker.stat_file(FileRequest(workspace_id=scope.workspace_id, path=request.path)).st_size
        if size > self.worker.config.max_artifact_bytes:
            raise ValueError('产物超过配置的字节上限')
        artifact = WorkspaceArtifact(path=request.path, size_bytes=size,
            media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        attachments = []
        media_status = 'not_applicable'
        if artifact.media_type.startswith('image/'):
            try:
                asset_id = await call.save_image(self.worker.read_bytes(
                    FileRequest(workspace_id=scope.workspace_id, path=request.path)),
                    f'工作空间图片产物：{request.path}')
                artifact = artifact.model_copy(update={'asset_id': asset_id})
                attachments.append(asset_id)
                media_status = 'registered'
            except (ValueError, OSError) as error:
                return {'status': 'partial', 'scene_id': scope.scene_id, 'job_id': scope.job_id,
                    'artifact': artifact.model_dump(mode='json'), 'attachments': [],
                    'media_status': 'registration_failed', 'media_error': str(error),
                    'note': '文件仍可由授权面板下载；图片未登记为可发送媒体。'}
        return {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'artifact': artifact.model_dump(mode='json'),
            'attachments': attachments,
            'media_status': media_status,
            'note': '普通文件保留在当前工作目录，由授权工作面板按工作归属读取；本工具不自动发送。'}

    async def read_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None:
            return None
        requester = job.get('requester_qq_uid')
        if not requester:
            return None
        panel_call = type('PanelCall', (), {'role': 'work', 'job_id': job_id,
            'requester_qq_uid': requester, 'scene_id': scene_id})()
        scope = await self.scope_for(panel_call, allow_terminal=True)
        self.worker.ensure_workspace_available(scope.workspace_id)
        file_request = WorkspaceFileInput(path=path, offset=offset, limit=limit)
        file_path = self.worker.file_path(scope.workspace_id, file_request.path)
        if file_path.is_symlink() or not file_path.is_file():
            raise ValueError('工作空间文件不存在或不是普通文件')
        content, next_offset = self.worker.read_text_range(
            FileRequest(workspace_id=scope.workspace_id, path=path), offset, limit)
        return {'scene_id': scene_id, 'job_id': job_id, 'path': path, 'content': content,
            'offset': offset, 'next_offset': next_offset, 'truncated': next_offset is not None}

    async def read_bytes_for_job(self, scene_id: str, job_id: str, path: str) -> tuple[bytes, str] | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        panel_call = type('PanelCall', (), {'role': 'work', 'job_id': job_id,
            'requester_qq_uid': job['requester_qq_uid'], 'scene_id': scene_id})()
        scope = await self.scope_for(panel_call, allow_terminal=True)
        self.worker.ensure_workspace_available(scope.workspace_id)
        data = self.worker.read_bytes(FileRequest(workspace_id=scope.workspace_id, path=path))
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        return data, mime

    async def list_for_job(self, scene_id: str, job_id: str) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        scope = WorkspaceScope(scene_id=scene_id, requester_qq_uid=job['requester_qq_uid'], job_id=job_id)
        artifacts = self._artifacts(scope)
        return {'scene_id': scene_id, 'job_id': job_id,
            'artifacts': [item.model_dump(mode='json') for item in artifacts],
            'truncated': len(artifacts) >= self.worker.config.max_artifact_files}


class GatewayWorkspaceService:
    """`run_python` through the isolated Worker Gateway; the host keeps no runtime.

    One formal backend: when the workspace plugin is configured with a
    gateway, every execution, listing and read goes through it, and a gateway
    failure is a real failure — never a silent fall back to a host
    ``docker run``.  The host keeps its own `execution_runs` row per
    submission, written before the request leaves, so a client timeout or a
    restart finds the same execution id instead of starting a second
    container.
    """

    scope_for = WorkspaceService.scope_for

    def __init__(self, client, config, event_store, plugin_id: str):
        self.client = client
        self.config = config
        self.event_store = event_store
        self.plugin_id = plugin_id
        self._run_locks: dict[str, asyncio.Lock] = {}

    def run_lock(self, workspace_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(workspace_id, asyncio.Lock())

    # ---- submission ---------------------------------------------------------
    async def run_python(self, call, request: RunPythonInput) -> dict:
        scope = await self.scope_for(call)
        async with self.run_lock(scope.workspace_id):
            job = await self.event_store.get_job(scope.job_id, call.scene_id)
            stored_initiator = (job or {}).get('initiator')
            if not job or stored_initiator is None:
                raise ValueError('该工作没有类型化发起者，不能通过网关执行')
            # The revision this call was admitted under, carried on the call
            # itself.  A tool call that waited on the run lock while the work
            # was revised must not have its old script filed under the new
            # revision: the two are different executions of different goals.
            expected_revision = getattr(call, 'job_revision', None)
            if expected_revision is not None and job['revision'] != expected_revision:
                raise ValueError(
                    f'该工具调用属于工作版本 {expected_revision}，当前工作已是版本 {job["revision"]}；'
                    '不能把旧脚本贴到新修订的身份上，请按当前目标重新发起')
            conclusion, detail = await self._reconcile_workspace(scope.workspace_id)
            if conclusion != 'available':
                raise ValueError(detail)
            input_files, manifest = await self._input_files(job, request.input_result_ids, call.scene_id)
            execution = ExecutionRequest(
                execution_id='x' + uuid.uuid4().hex,
                scene_id=call.scene_id, job_id=scope.job_id, job_revision=job['revision'],
                workspace_id=scope.workspace_id,
                initiator=TypeAdapter(Initiator).validate_python(stored_initiator),
                worker_type='python', script=request.script,
                image_ref=self.config.image_ref, network_policy=self.config.network_policy,
                input_files=input_files,
                deadline_seconds=self._deadline_seconds(job))
            # The host row exists before the wire request: a timeout after this
            # point is "query the same id", never "submit a fresh one".
            record, _created = await self.event_store.record_execution(execution)
            await self._submit_once(execution, scope)
            final = await self._await_result(execution.execution_id, record.deadline_at, scope)
            result = self._execution_result(execution.execution_id, final, scope)
            try:
                listed, truncated = await self._artifacts_summary(execution.execution_id)
                artifacts_known = True
            except RuntimeError as error:
                # The execution itself has an answer; only the file listing
                # does not.  Saying that plainly is not the same as an empty
                # directory, and it must not turn a finished run into a failure.
                listed, truncated, artifacts_known = [], None, False
                result['artifacts_error'] = str(error)
            result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'input_manifest': manifest, 'artifacts': listed,
                'artifacts_known': artifacts_known,
                'artifacts_truncated': bool(truncated) if artifacts_known else None}
            return result

    async def _submit_once(self, execution: ExecutionRequest, scope) -> None:
        """Send one submission, under the same cancellation path as the poll.

        From the moment the host owns an execution id the cancellation path is
        one continuous one: a cancel that arrives while the request is still on
        the wire is not allowed to skip the stop, and it never resubmits the
        script.  The outcomes are kept apart rather than flattened:

        * an answered refusal means the Gateway rejected this request before
          starting anything, so this id has no execution to query;
        * a conflict means the id already exists on the Gateway — the poll
          below reads that same execution instead of starting a second one;
        * a transmission failure or an unreadable answer proves nothing about
          whether the request arrived, so the id is kept and only polled.

        Only a cancellation that happens before the request could have left is
        answered as "nothing was sent"; after that, the honest answer is the
        one the Gateway returns for that id.
        """
        try:
            await self.client.submit(execution)
        except GatewayConflict:
            # Same id upstream: not an error and not a reason to start again.
            return
        except GatewayRefused as error:
            await self._mark_refused(execution.execution_id, str(error))
            raise ValueError(str(error)) from None
        except (GatewayUnavailable, GatewayResultUnknown):
            return
        except asyncio.CancelledError:
            termination = await asyncio.shield(
                self._cancel_execution(execution.execution_id, scope))
            raise WorkspaceCancelled(termination) from None

    async def _input_files(self, job, result_ids, scene_id):
        if len(result_ids) > 8:
            raise ValueError('一次执行最多导出 8 份已取得资料')
        allowed = set(job.get('result_ids', ()))
        files, manifest = [], []
        for index, result_id in enumerate(dict.fromkeys(result_ids), 1):
            if result_id not in allowed:
                raise ValueError(f'资料 {result_id} 尚未登记为当前工作的资料')
            observation = await self.event_store.read_tool_observation(result_id, [scene_id])
            if observation is None:
                raise ValueError(f'资料 {result_id} 不属于当前场景或已不存在')
            name = f'result_{index}.txt'
            files.append(ExecutionInputFile(name=name, text=observation.content))
            manifest.append({'result_id': result_id, 'path': f'/lenbot-control/input/{name}',
                'coverage': observation.coverage, 'status': observation.status,
                'sources': [source.model_dump(mode='json') for source in observation.sources]})
        files.append(ExecutionInputFile(name='manifest.json',
                                        text=json.dumps(manifest, ensure_ascii=False, indent=2)))
        return files, manifest

    def _deadline_seconds(self, job) -> float:
        seconds = self.config.execution_timeout_seconds
        deadline_at = (job.get('budget') or {}).get('deadline_at')
        if deadline_at is not None:
            remaining = deadline_at - self.event_store.clock()
            if remaining <= 1:
                raise ValueError('当前工作剩余期限不足，无法开始新的执行')
            seconds = min(seconds, remaining)
        return max(1.0, min(seconds, 3600.0))

    async def _await_result(self, execution_id: str, deadline_at: float, scope):
        """Poll the same execution id until it ends, its deadline passes, or we are cancelled.

        The stop instant is computed from the execution's own absolute deadline
        — the one written when it was accepted — plus one request round trip
        for the final read.  Neither a slow submit nor a retry hands out extra
        execution time.
        """
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + max(0.0, deadline_at - self.event_store.clock()) \
            + self.config.request_timeout_seconds + 30.0
        try:
            while True:
                record = None
                try:
                    record = await self.client.get(execution_id)
                except (GatewayUnavailable, GatewayResultUnknown):
                    pass
                except GatewayRefused as error:
                    raise RuntimeError(f'网关无法定位执行 {execution_id}：{error}') from None
                if record is not None:
                    await self._mirror_terminal(record)
                    if is_terminal(record.state):
                        return record
                if loop.time() >= stop_at:
                    return None
                await asyncio.sleep(self.config.poll_interval_seconds)
        except asyncio.CancelledError:
            termination = await asyncio.shield(self._cancel_execution(execution_id, scope))
            raise WorkspaceCancelled(termination) from None

    async def _cancel_execution(self, execution_id: str, scope) -> dict:
        try:
            outcome = await self.client.cancel(execution_id, '工作执行被取消')
        except (GatewayUnavailable, GatewayResultUnknown, GatewayRefused, GatewayConflict) as error:
            termination = {'status': 'unconfirmed', 'detail': f'取消请求未得到网关确认：{error}'}
            park_termination(scope.workspace_id, termination)
            return termination
        await self._mirror_terminal(outcome.record)
        termination = (outcome.termination.model_dump(mode='json') if outcome.termination
                       else outcome.record.termination.model_dump(mode='json')
                       if outcome.record.termination else
                       {'status': 'confirmed_stopped', 'detail': f'执行已自行结束（{outcome.record.state.value}）'}
                       if outcome.record.state in {ExecutionState.EXITED, ExecutionState.FAILED}
                       else {'status': 'unconfirmed', 'detail': '网关未返回终止回执'})
        park_termination(scope.workspace_id, termination)
        return termination

    def _execution_result(self, execution_id: str, record, scope) -> dict:
        if record is None:
            return {'status': 'error', 'workspace_id': scope.workspace_id, 'execution_id': execution_id,
                    'returncode': None, 'stdout': '', 'stderr': '',
                    'stdout_truncated': False, 'stderr_truncated': False,
                    'error': f'执行 {execution_id} 结果未知：网关未在期限内给出终态；'
                             '同一执行 ID 后续只核对，不会重复启动'}
        base = {'workspace_id': scope.workspace_id, 'execution_id': execution_id,
                'returncode': record.returncode, 'stdout': record.stdout, 'stderr': record.stderr,
                'stdout_truncated': record.stdout_truncated, 'stderr_truncated': record.stderr_truncated}
        if record.state is ExecutionState.EXITED:
            return {'status': 'ok', **base}
        if record.state is ExecutionState.FAILED:
            return {'status': 'error', **base, 'error': record.error or '执行以失败结束'}
        termination = record.termination.model_dump(mode='json') if record.termination else None
        return {'status': 'error', **base,
                'error': record.error or f'执行被网关停止（{record.state.value}）',
                **({'termination': termination} if termination else {})}

    # ---- host journal mirroring ---------------------------------------------
    async def _mirror_terminal(self, record) -> None:
        host = await self.event_store.get_execution(record.execution_id)
        if host is None or not is_terminal(record.state) or host.state == record.state:
            return
        if is_terminal(host.state) and not (host.state is ExecutionState.TERMINATION_UNCONFIRMED
                                            and record.state is ExecutionState.TERMINATION_CONFIRMED):
            return
        if record.state in {ExecutionState.EXITED, ExecutionState.FAILED}:
            await self.event_store.append_execution_event(
                record.execution_id, 'gateway_result', f'网关回读终态 {record.state.value}',
                state=record.state, returncode=record.returncode, error=record.error,
                stdout=record.stdout, stderr=record.stderr,
                stdout_truncated=record.stdout_truncated, stderr_truncated=record.stderr_truncated)
            return
        if host.state not in {ExecutionState.CANCEL_REQUESTED, ExecutionState.TERMINATION_UNCONFIRMED}:
            await self.event_store.append_execution_event(
                record.execution_id, 'gateway_result', '网关记录了停止请求',
                state=ExecutionState.CANCEL_REQUESTED)
        await self.event_store.append_execution_event(
            record.execution_id, 'gateway_result', f'网关回读终止结果 {record.state.value}',
            state=record.state, termination=record.termination, error=record.error)

    async def _mark_refused(self, execution_id: str, detail: str) -> None:
        try:
            await self.event_store.append_execution_event(
                execution_id, 'gateway_refused', detail, state=ExecutionState.FAILED, error=detail[:2000])
        except ValueError:
            pass

    async def _reconcile_workspace(self, workspace_id: str) -> tuple[str, str]:
        """Settle host rows whose gateway outcome is already knowable.

        A crash between the host row and the wire request leaves an accepted
        row the gateway never saw; a crash while polling leaves one the gateway
        finished on its own.  Both are read back here, never re-run.

        The answer is one of three executable conclusions, not a log line:

        * ``available`` — nothing in this workspace is still occupiable, so a
          new execution may be submitted;
        * ``occupied`` — a row is still running, or its stop is unconfirmed;
        * ``unknown`` — the Gateway could not be asked, so nothing about the
          workspace is established and nothing new may start in it.

        Each row is closed by what its own answer means.  A 404 says this
        Gateway's journal has no record of that id, which is not proof that no
        side effect ever happened elsewhere; the row is recorded as unrecorded
        rather than as never having run.  An authentication or other 4xx
        answer is about the request, not about the execution, and is never
        folded into "it never started".
        """
        blocked, blocking = False, None
        for host in await self.event_store.executions_for_workspace(workspace_id):
            if is_terminal(host.state) and host.state is not ExecutionState.TERMINATION_UNCONFIRMED:
                # A confirmed end no longer occupies the workspace.  An
                # unconfirmed termination is terminal yet still owes an answer,
                # so it is rechecked below instead of being skipped.
                continue
            try:
                record = await self.client.get(host.execution_id)
            except GatewayUnavailable:
                return 'unknown', f'网关暂时不可达，工作区 {workspace_id} 的占用无法核实；本次不开始新执行'
            except GatewayResultUnknown as error:
                return 'unknown', f'网关未能确认工作区 {workspace_id} 的执行结果：{error}；本次不开始新执行'
            except GatewayConflict as error:
                await self._mark_unresolved(host.execution_id, f'身份冲突：{error}')
                return 'unknown', f'工作区 {workspace_id} 的执行身份冲突；保持占用，等待运营者核对'
            except GatewayRefused as error:
                if error.status_code == 404:
                    await self._mark_unrecorded(
                        host.execution_id, f'网关日志中没有该执行（HTTP 404）：{error.detail}')
                else:
                    await self._mark_unresolved(
                        host.execution_id, f'网关拒绝了查询（HTTP {error.status_code}）：{error.detail}')
                    return 'unknown', (f'网关未接受对工作区 {workspace_id} 的核对'
                                       f'（HTTP {error.status_code}）：{error.detail}；本次不开始新执行')
                continue
            await self._mirror_terminal(record)
            if record.state is ExecutionState.TERMINATION_UNCONFIRMED:
                blocked, blocking = True, record.execution_id
        if blocked:
            return 'occupied', (f'工作区 {workspace_id} 的执行 {blocking} 终止未确认，仍不能新用；'
                                '请先由运营者核对容器并确认终止')
        return 'available', '工作区可用'

    async def _mark_unrecorded(self, execution_id: str, detail: str) -> None:
        """Close a row the Gateway's own journal has no record of.

        That is the Gateway's answer about its log, not a proof about the
        world: the row is closed as failed with the answer it actually got, so
        a later reader is never told that nothing ever happened.
        """
        try:
            await self.event_store.append_execution_event(
                execution_id, 'gateway_unrecorded', detail,
                state=ExecutionState.FAILED, error=detail[:2000])
        except ValueError:
            pass

    async def _mark_unresolved(self, execution_id: str, detail: str) -> None:
        """The Gateway could not answer about this id; the row keeps occupying."""
        try:
            await self.event_store.append_execution_event(execution_id, 'gateway_unresolved', detail)
        except ValueError:
            pass

    # ---- artifact reads ------------------------------------------------------
    async def _artifacts_summary(self, execution_id: str) -> tuple[list[dict], bool | None]:
        """This execution's registered outputs, and whether the listing was cut.

        An unreachable Gateway is "not readable yet", not an empty directory:
        the two are returned differently so a caller cannot report a failed
        listing as a work that produced nothing.
        """
        try:
            listing = await self.client.artifacts(execution_id)
        except (GatewayRefused, GatewayUnavailable, GatewayResultUnknown) as error:
            raise RuntimeError(f'网关暂时不可用，产物清单暂不可读：{error}') from None
        return ([{'path': item['path'], 'size_bytes': item['size_bytes'],
                  'media_type': item['media_type'], 'artifact_id': item['artifact_id'],
                  'over_limit': False} for item in listing.get('artifacts', [])],
                listing.get('truncated'))

    async def _listing_for(self, execution_id: str) -> dict | None:
        """One execution's listing, or None when the Gateway has no such log."""
        try:
            return await self.client.artifacts(execution_id)
        except GatewayRefused:
            return None
        except (GatewayUnavailable, GatewayResultUnknown) as error:
            raise RuntimeError(f'网关暂时不可用，产物暂不可读：{error}') from None

    async def _snapshot_execution(self, scene_id: str, job_id: str):
        """The execution whose registered outputs are this job's current files.

        The current workspace is a *confirmed* snapshot: the newest execution
        the Gateway actually finished.  A row that was refused, or that is
        still running or unknown, has no confirmed outputs yet, so the current
        listing must not fall back to an older execution's files and must not
        report "no files" either — it reports that the snapshot is not
        readable yet.
        """
        records = await self.event_store.executions_for_job(scene_id, job_id)
        for host in reversed(records):
            if not is_terminal(host.state):
                continue
            listing = await self._listing_for(host.execution_id)
            if listing is None:
                continue
            return host, listing
        pending = [host for host in records
                   if host.state in {ExecutionState.ACCEPTED, ExecutionState.STARTING,
                                     ExecutionState.RUNNING, ExecutionState.CANCEL_REQUESTED}]
        if pending:
            raise RuntimeError('当前工作区还没有已确认的执行快照，产物暂不可读')
        return None, None

    async def _artifact_by_path(self, scene_id: str, job_id: str, path: str,
                                *, execution_id: str | None = None) -> dict | None:
        """One artifact identity, from the current snapshot or a named execution.

        Reading the current workspace never silently walks back into an older
        execution: a file the newest confirmed execution no longer produces is
        gone from the snapshot, and finding it again requires naming the
        execution that did produce it.
        """
        _validate_relative_path(path)
        if execution_id is not None:
            host = await self.event_store.get_execution(execution_id)
            if host is None or host.scene_id != scene_id or host.job_id != job_id:
                raise ValueError('指定的执行不属于当前工作')
            listing = await self._listing_for(execution_id)
            if listing is None:
                raise ValueError('指定的执行不在网关日志中，其产物身份无法确认')
            for item in listing.get('artifacts', []):
                if item.get('path') == path:
                    return item
            return None
        _host, listing = await self._snapshot_execution(scene_id, job_id)
        if listing is None:
            raise ValueError('该工作还没有已确认的执行快照，无法读取当前产物')
        for item in listing.get('artifacts', []):
            if item.get('path') == path:
                return item
        return None

    async def _artifact_text(self, scene_id: str, job_id: str, path: str, offset: int, limit: int,
                             *, execution_id: str | None = None) -> dict:
        """A page of one artifact, decoded as text from a bounded byte range.

        The page is a character range and the bytes are read from the Gateway
        as they are needed, so a small page never pulls a whole large file.
        Decoding is incremental: only complete UTF-8 sequences are handed to
        the page, so a Chinese character is never split across a page boundary
        and the page coordinates stay one continuous character sequence.
        """
        if offset < 0 or limit < 1:
            raise ValueError('invalid text range')
        artifact = await self._artifact_by_path(scene_id, job_id, path, execution_id=execution_id)
        if artifact is None:
            raise ValueError('工作空间文件不存在或尚未登记为产物')
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        collected: list[str] = []
        total = 0
        async for chunk in self.client.artifact_chunks(artifact['artifact_id']):
            collected.append(decoder.decode(chunk, final=False))
            total = sum(map(len, collected))
            # One chunk past the page is enough to tell that more follows; the
            # reader stops there instead of walking the rest of the file.
            if total > offset + limit:
                break
        else:
            collected.append(decoder.decode(b'', final=True))
        text = ''.join(collected)
        piece = text[offset:offset + limit]
        reach = offset + len(piece) if len(text) > offset + len(piece) else None
        return {'scene_id': scene_id, 'job_id': job_id, 'path': path, 'content': piece,
                'offset': offset, 'next_offset': reach, 'truncated': reach is not None}

    # ---- tool entries --------------------------------------------------------
    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        _host, listing = await self._snapshot_execution(scope.scene_id, scope.job_id)
        if listing is None:
            raise RuntimeError('该工作还没有已确认的执行快照，文件清单暂不可读')
        return {'workspace_id': scope.workspace_id, 'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'files': sorted(item['path'] for item in listing.get('artifacts', [])),
                'truncated': bool(listing.get('truncated'))}

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        return await self._artifact_text(scope.scene_id, scope.job_id, request.path,
                                         request.offset, request.limit,
                                         execution_id=request.execution_id)

    async def export_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        row = await self._artifact_by_path(scope.scene_id, scope.job_id, request.path)
        if row is None:
            raise ValueError('工作空间文件不存在或尚未登记为产物')
        artifact = WorkspaceArtifact(path=request.path, size_bytes=row['size_bytes'],
            media_type=row['media_type'] or mimetypes.guess_type(request.path)[0] or 'application/octet-stream')
        attachments = []
        media_status = 'not_applicable'
        if artifact.media_type.startswith('image/'):
            try:
                buffer = bytearray()
                async for chunk in self.client.artifact_chunks(row['artifact_id']):
                    buffer.extend(chunk)
                asset_id = await call.save_image(bytes(buffer), f'工作空间图片产物：{request.path}')
                artifact = artifact.model_copy(update={'asset_id': asset_id})
                attachments.append(asset_id)
                media_status = 'registered'
            except (ValueError, OSError, RuntimeError) as error:
                return {'status': 'partial', 'scene_id': scope.scene_id, 'job_id': scope.job_id,
                    'artifact': artifact.model_dump(mode='json'), 'attachments': [],
                    'media_status': 'registration_failed', 'media_error': str(error),
                    'note': '文件仍可由授权面板下载；图片未登记为可发送媒体。'}
        return {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'artifact': artifact.model_dump(mode='json'),
            'attachments': attachments,
            'media_status': media_status,
            'note': '普通文件保留在网关产物区，由授权工作面板按工作归属读取；本工具不自动发送。'}

    # ---- panel entries -------------------------------------------------------
    async def _panel_scope(self, scene_id: str, job_id: str):
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        panel_call = type('PanelCall', (), {'role': 'work', 'job_id': job_id,
            'requester_qq_uid': job['requester_qq_uid'], 'scene_id': scene_id})()
        return await self.scope_for(panel_call, allow_terminal=True)

    async def read_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int) -> dict | None:
        scope = await self._panel_scope(scene_id, job_id)
        if scope is None:
            return None
        return await self._artifact_text(scene_id, job_id, path, offset, limit)

    async def read_bytes_for_job(self, scene_id: str, job_id: str, path: str) -> tuple[bytes, str] | None:
        scope = await self._panel_scope(scene_id, job_id)
        if scope is None:
            return None
        row = await self._artifact_by_path(scene_id, job_id, path)
        if row is None:
            raise ValueError('工作空间文件不存在或尚未登记为产物')
        buffer = bytearray()
        async for chunk in self.client.artifact_chunks(row['artifact_id']):
            buffer.extend(chunk)
        data = bytes(buffer)
        return data, row['media_type'] or 'application/octet-stream'

    async def list_for_job(self, scene_id: str, job_id: str) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        _host, listing = await self._snapshot_execution(scene_id, job_id)
        if listing is None:
            # No confirmed snapshot yet: an honest "not readable", never an
            # empty directory that reads as "this work produced nothing".
            raise RuntimeError('该工作还没有已确认的执行快照，产物清单暂不可读')
        artifacts = [WorkspaceArtifact(path=item['path'], size_bytes=item['size_bytes'],
                                       media_type=item['media_type']).model_dump(mode='json')
                     for item in listing.get('artifacts', [])]
        return {'scene_id': scene_id, 'job_id': job_id, 'artifacts': artifacts,
                'truncated': bool(listing.get('truncated'))}
