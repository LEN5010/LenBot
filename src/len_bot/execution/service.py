"""Host-owned workspace admission, input export and artifact checks."""
from __future__ import annotations

import asyncio
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
            await self._reconcile_workspace(scope.workspace_id)
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
            await self.event_store.record_execution(execution)
            try:
                await self.client.submit(execution)
            except (GatewayRefused, GatewayConflict) as error:
                await self._mark_refused(execution.execution_id, str(error))
                raise ValueError(str(error)) from None
            except (GatewayUnavailable, GatewayResultUnknown):
                pass  # the execution may exist; the poll below reads the same id
            record = await self._await_result(execution.execution_id, execution.deadline_seconds, scope)
            result = self._execution_result(execution.execution_id, record, scope)
            result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'input_manifest': manifest,
                'artifacts': await self._artifacts_summary(execution.execution_id),
                'artifacts_truncated': False}
            return result

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

    async def _await_result(self, execution_id: str, deadline_seconds: float, scope):
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + deadline_seconds + self.config.request_timeout_seconds + 30.0
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

    async def _reconcile_workspace(self, workspace_id: str) -> None:
        """Settle host rows whose gateway outcome is already knowable.

        A crash between the host row and the wire request leaves an accepted
        row the gateway never saw; a crash while polling leaves one the
        gateway finished on its own.  Both are read back here, never re-run.
        """
        for host in await self.event_store.executions_for_workspace(workspace_id):
            if is_terminal(host.state):
                continue
            try:
                record = await self.client.get(host.execution_id)
            except (GatewayUnavailable, GatewayResultUnknown):
                continue
            except GatewayRefused:
                await self._mark_refused(host.execution_id, '网关执行日志中没有该执行；请求从未到达，不再启动')
                continue
            await self._mirror_terminal(record)

    # ---- artifact reads ------------------------------------------------------
    async def _artifacts_summary(self, execution_id: str) -> list[dict]:
        try:
            listing = await self.client.artifacts(execution_id)
        except (GatewayRefused, GatewayUnavailable, GatewayResultUnknown):
            return []
        return [{'path': item['path'], 'size_bytes': item['size_bytes'],
                 'media_type': item['media_type'], 'artifact_id': item['artifact_id'],
                 'over_limit': False} for item in listing.get('artifacts', [])]

    async def _latest_artifacts(self, scene_id: str, job_id: str) -> list[dict]:
        """The newest execution's registered outputs: the directory's end state."""
        records = await self.event_store.executions_for_job(scene_id, job_id)
        for host in reversed(records):
            try:
                listing = await self.client.artifacts(host.execution_id)
            except (GatewayUnavailable, GatewayResultUnknown) as error:
                raise RuntimeError(f'网关暂时不可用，产物暂不可读：{error}') from None
            except GatewayRefused:
                continue
            return listing.get('artifacts', [])
        return []

    async def _artifact_by_path(self, scene_id: str, job_id: str, path: str) -> dict | None:
        _validate_relative_path(path)
        records = await self.event_store.executions_for_job(scene_id, job_id)
        for host in reversed(records):
            try:
                listing = await self.client.artifacts(host.execution_id)
            except (GatewayUnavailable, GatewayResultUnknown) as error:
                raise RuntimeError(f'网关暂时不可用，产物暂不可读：{error}') from None
            except GatewayRefused:
                continue
            for item in listing.get('artifacts', []):
                if item.get('path') == path:
                    return item
        return None

    async def _artifact_text(self, scene_id: str, job_id: str, path: str, offset: int, limit: int) -> dict:
        if offset < 0 or limit < 1:
            raise ValueError('invalid text range')
        artifact = await self._artifact_by_path(scene_id, job_id, path)
        if artifact is None:
            raise ValueError('工作空间文件不存在或尚未登记为产物')
        data, _media_type = await self.client.artifact_bytes(artifact['artifact_id'])
        text = data.decode('utf-8', 'replace')
        piece = text[offset:offset + limit]
        next_offset = offset + len(piece) if offset + len(piece) < len(text) else None
        return {'scene_id': scene_id, 'job_id': job_id, 'path': path, 'content': piece,
                'offset': offset, 'next_offset': next_offset, 'truncated': next_offset is not None}

    # ---- tool entries --------------------------------------------------------
    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        listing = await self._latest_artifacts(scope.scene_id, scope.job_id)
        return {'workspace_id': scope.workspace_id, 'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'files': sorted(item['path'] for item in listing), 'truncated': False}

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        return await self._artifact_text(scope.scene_id, scope.job_id, request.path,
                                         request.offset, request.limit)

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
                data, _media_type = await self.client.artifact_bytes(row['artifact_id'])
                asset_id = await call.save_image(data, f'工作空间图片产物：{request.path}')
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
        data, media_type = await self.client.artifact_bytes(row['artifact_id'])
        return data, row['media_type'] or media_type or 'application/octet-stream'

    async def list_for_job(self, scene_id: str, job_id: str) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        listing = await self._latest_artifacts(scene_id, job_id)
        artifacts = [WorkspaceArtifact(path=item['path'], size_bytes=item['size_bytes'],
                                       media_type=item['media_type']).model_dump(mode='json')
                     for item in listing]
        return {'scene_id': scene_id, 'job_id': job_id, 'artifacts': artifacts, 'truncated': False}
