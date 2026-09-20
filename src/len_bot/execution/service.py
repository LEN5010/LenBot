"""Host-owned workspace admission, input export and artifact checks."""
from __future__ import annotations

import asyncio
import codecs
import json
import mimetypes
import shutil
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from len_bot.events.models import Initiator
from len_bot.execution.admission import require_execution_job
from len_bot.execution.client import (
    GatewayConflict, GatewayRefused, GatewayResultUnknown, GatewayUnavailable,
)
from len_bot.execution.inputs import (
    collect_input_entries, manifest_of, wire_input_files,
)
from len_bot.execution.models import RunPythonInput, WorkspaceArtifact, WorkspaceFileInput, WorkspaceScope
from len_bot.execution.protocol import OCCUPYING_STATES, ExecutionRequest, ExecutionState, is_terminal
from len_bot.execution.workspace import (
    FileRequest, RunPythonRequest, WorkspaceCancelled, WorkspaceRequest, WorkspaceWorker,
    _validate_relative_path, park_termination,
)


class WorkspaceService:
    def __init__(self, worker: WorkspaceWorker, event_store, plugin_id: str,
                 media_service=None):
        self.worker = worker
        self.event_store = event_store
        self.plugin_id = plugin_id
        # Only the panel's backend reader may be built without it; a service
        # that was given no media reader refuses attachment imports instead of
        # silently exporting the work's observations only.
        self.media_service = media_service

    async def scope_for(self, call, *, allow_terminal: bool = False) -> WorkspaceScope:
        if call.role != 'work' or not call.job_id:
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
        if call.requester_qq_uid:
            return WorkspaceScope(scene_id=call.scene_id, requester_qq_uid=call.requester_qq_uid, job_id=call.job_id)
        from len_bot.runtime.public_research import verify_public_job
        if not await verify_public_job(self.event_store, job):
            raise ValueError('系统工作区没有已验证的公共研究归属')
        return WorkspaceScope(scene_id=call.scene_id, system_subject=job['initiator']['agent_id'], job_id=call.job_id)

    async def _export_inputs(self, scope: WorkspaceScope, request: RunPythonInput, scene_id: str, control: Path) -> dict:
        """Write this request's inputs into the read-only control area.

        The manifest is a statement about *this* export: which observation or
        asset id each file came from, its coverage, and how many bytes were
        actually written.  Nothing is exported that this work never referred
        to, and a named input that cannot be read aborts the export instead of
        leaving a partial input directory behind.
        """
        directory = control / 'input'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = await self.event_store.get_job(scope.job_id, scene_id)
        if job is None:
            raise ValueError('原工作已不存在，不能导出执行输入')
        entries = await collect_input_entries(self.event_store, job, scope.job_id, scene_id,
            request.input_result_ids, request.input_asset_ids, self._read_asset(scene_id))
        for entry in entries:
            path = directory / entry['name']
            if entry['kind'] == 'text':
                self.worker._write_control(path, entry['text'])
            else:
                self.worker._write_control_bytes(path, entry['data'])
        manifest = manifest_of(entries, scope.job_id, scene_id, job['revision'])
        self.worker._write_control(control / 'manifest.json',
            json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

    def _read_asset(self, scene_id: str):
        """The scoped media read this service exports attachments through.

        A service built without a media reader (the panel's read-only backend)
        never reaches this path: it refuses the import instead of exporting a
        work's observations and quietly dropping its pictures.
        """
        async def read(asset_id: str):
            if self.media_service is None:
                raise ValueError('当前后端没有配置媒体读取，不能导入图片附件')
            return await self.media_service.read_image_bytes(asset_id, scene_id)
        return read

    def _artifacts(self, scope: WorkspaceScope) -> tuple[list[WorkspaceArtifact], bool]:
        self.worker.ensure_workspace_available(scope.workspace_id)
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
        limit = self.worker.config.max_artifact_files
        return artifacts[:limit], len(artifacts) > limit

    async def run_python(self, call, request: RunPythonInput) -> dict:
        scope = await self.scope_for(call)
        async with self.worker.run_lock(scope.workspace_id):
            await require_execution_job(self.event_store, call, self.plugin_id)
            self.worker.ensure_workspace_available(scope.workspace_id)
            control = self.worker.control_directory(scope.workspace_id) / ('input-' + uuid.uuid4().hex)
            control.mkdir(mode=0o700)
            try:
                manifest = await self._export_inputs(scope, request, call.scene_id, control)
                async def admit():
                    return await require_execution_job(self.event_store, call, self.plugin_id)
                result = await self.worker._run_python_locked(
                    RunPythonRequest(workspace_id=scope.workspace_id, script=request.script),
                    control=control, admission=admit, clock=self.event_store.clock)
            finally:
                # An unknown surviving container may still own its mount.
                # Keep that input snapshot until termination is confirmed.
                if not (self.worker.control_root / scope.workspace_id / '.termination_unconfirmed').exists():
                    shutil.rmtree(control)
            try:
                artifacts, truncated = self._artifacts(scope)
                artifacts_known = True
            except (ValueError, OSError) as error:
                artifacts, truncated, artifacts_known = [], None, False
                result['artifacts_error'] = str(error)
        result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'input_manifest': manifest, 'artifacts': [item.model_dump(mode='json') for item in artifacts],
            'artifacts_known': artifacts_known, 'artifacts_truncated': truncated,
            'snapshot_kind': 'live_workspace'}
        return result

    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        result = self.worker.list_files(WorkspaceRequest(workspace_id=scope.workspace_id))
        result.update({'scene_id': scope.scene_id, 'job_id': scope.job_id})
        return result

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        if request.execution_id is not None:
            raise ValueError('宿主 worker 文件不接受 Gateway execution_id')
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
        if request.execution_id is not None:
            raise ValueError('宿主 worker 文件不接受 Gateway execution_id')
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

    async def read_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int,
                           execution_id: str | None = None) -> dict | None:
        if execution_id is not None:
            raise ValueError('宿主 worker 文件不接受 Gateway execution_id')
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None:
            return None
        requester = job.get('requester_qq_uid')
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

    async def read_bytes_for_job(self, scene_id: str, job_id: str, path: str,
                                execution_id: str | None = None) -> tuple[bytes, str] | None:
        if execution_id is not None:
            raise ValueError('宿主 worker 文件不接受 Gateway execution_id')
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None:
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
        if job is None:
            return None
        panel_call = type('PanelCall', (), {'role': 'work', 'job_id': job_id,
            'requester_qq_uid': job['requester_qq_uid'], 'scene_id': scene_id})()
        scope = await self.scope_for(panel_call, allow_terminal=True)
        artifacts, truncated = self._artifacts(scope)
        return {'scene_id': scene_id, 'job_id': job_id,
            'artifacts': [item.model_dump(mode='json') for item in artifacts],
            'snapshot_kind': 'live_workspace', 'truncated': truncated,
            'sampled_at': self.event_store.clock(),
            'truncated_reason': 'artifact_cap' if truncated else None}


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
    _read_asset = WorkspaceService._read_asset

    def __init__(self, client, config, event_store, plugin_id: str, media_service=None, action_reviewer=None):
        self.client = client
        self.config = config
        self.event_store = event_store
        self.plugin_id = plugin_id
        self.media_service = media_service
        self.action_reviewer = action_reviewer
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
            await require_execution_job(self.event_store, call, self.plugin_id)
            conclusion, detail = await self._reconcile_workspace(scope.workspace_id)
            if conclusion != 'available':
                raise ValueError(detail)
            input_files, manifest, input_assets = await self._input_files(job, request, call.scene_id)
            job = await require_execution_job(self.event_store, call, self.plugin_id)
            stored_initiator = job.get('initiator')
            initiator = TypeAdapter(Initiator).validate_python(stored_initiator)
            # Egress is authorized here, by the host, and travels as a plain
            # fact the Gateway can check before it starts anything.  Two
            # answers have to be yes: the run's own initiator holds a current
            # `network_python` grant, and the run carries no group material of
            # its own — an imported picture is exactly the private data the
            # plan keeps offline, and uploading it to a public address is a
            # data export that needs its own scope, which does not exist yet.
            # The Gateway refuses a forwarding policy without this answer, so
            # a deployment that has built egress cannot start a run the host
            # did not authorize.
            reviewed = await self._egress_authorized(job, call, initiator, request, input_assets)
            job = await require_execution_job(self.event_store, call, self.plugin_id)
            execution = ExecutionRequest(
                execution_id='x' + uuid.uuid4().hex,
                scene_id=call.scene_id, job_id=scope.job_id, job_revision=job['revision'],
                workspace_id=scope.workspace_id,
                initiator=initiator,
                worker_type='python', script=request.script,
                image_ref=self.config.image_ref, network_policy=self.config.network_policy,
                egress_authorized=reviewed is not None,
                review_action_id=reviewed.action_id if reviewed else None,
                input_assets=input_assets,
                input_files=input_files,
                deadline_seconds=self._deadline_seconds(job),
                deadline_at=(job.get('budget') or {}).get('deadline_at'))
            # The host row exists before the wire request: a timeout after this
            # point is "query the same id", never "submit a fresh one".
            record, _created = await self.event_store.record_execution(execution)
            await self._submit_once(execution, scope)
            final = await self._await_result(execution.execution_id, record.deadline_at, scope)
            result = self._execution_result(execution.execution_id, final, scope)
            if final is None or final.state in OCCUPYING_STATES:
                listed, truncated, artifacts_known = [], False, False
                result['artifacts_error'] = '此执行尚无已确认的终态产物快照；不能据此报告没有文件，原执行不会自动重跑'
            else:
                try:
                    listed, truncated, artifacts_known = await self._artifacts_summary(execution.execution_id)
                except RuntimeError as error:
                    # Keep the process outcome when its separate listing fails.
                    listed, truncated, artifacts_known = [], False, False
                    result['artifacts_error'] = str(error)
            result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'input_manifest': manifest, 'artifacts': listed,
                'artifacts_known': artifacts_known,
                'snapshot_kind': 'execution_artifacts',
                'artifacts_truncated': truncated if artifacts_known else None}
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

    async def _egress_authorized(self, job, call, initiator, request, input_assets):
        if not self.config.network_python_enabled:
            return None
        from len_bot.events.models import SystemInitiator
        from len_bot.runtime.public_research import verify_public_job
        from len_bot.memory.interests import InterestStore
        from len_bot.runtime.capabilities import Capability, subject_for
        if not isinstance(initiator, SystemInitiator) or not await verify_public_job(self.event_store, job):
            raise ValueError('联网 Python 只接受已验证的独立公共研究工作')
        if input_assets or request.input_asset_ids:
            raise ValueError('联网 Python 不接受附件或账号资料')
        ids = set(request.input_result_ids)
        if ids != await InterestStore(self.event_store).public_observation_ids(list(ids)):
            raise ValueError('联网 Python 的全部输入必须有可追溯的匿名公共来源')
        authority = getattr(self.event_store, 'capability_authority', None)
        if authority is None or self.action_reviewer is None:
            raise ValueError('联网 Python 缺少当前授权或动作审查入口')
        subject = subject_for(initiator, call.scene_id)
        def require_grant():
            decision = authority.check(Capability.NETWORK_PYTHON, subject, now=self.event_store.clock())
            if not decision.allowed:
                raise ValueError('当前系统主体没有联网 Python 授权')
        require_grant()
        parameters = {**request.model_dump(mode='json'), 'image_ref': self.config.image_ref,
            'network_policy': self.config.network_policy}
        action = await self.action_reviewer.request(job=job, native_call_id=call.tool_call_id,
            action_type='network_python', target=self.config.network_policy,
            parameters=parameters, result_ids=request.input_result_ids)
        await self.action_reviewer.approve(action)
        require_grant()
        return action

    async def _input_files(self, job, request: RunPythonInput, scene_id):
        """This request's inputs as wire files, its manifest, and its provenance.

        The bytes come from the work's own saved sources; the Gateway only ever
        writes them down.  ``input_assets`` names the media ids this export
        actually read, so the execution's stored request says where a picture
        came from even though the Gateway never fetches one by id.
        """
        entries = await collect_input_entries(self.event_store, job, job['id'], scene_id,
            request.input_result_ids, request.input_asset_ids, self._read_asset(scene_id))
        assets = [entry['asset_id'] for entry in entries if entry['kind'] == 'asset']
        manifest = manifest_of(entries, job['id'], scene_id, job['revision'])
        return wire_input_files(entries, manifest), manifest, assets

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
        if host is None or not is_terminal(record.state):
            return
        same_state = host.state == record.state
        can_confirm = (host.state is ExecutionState.TERMINATION_UNCONFIRMED
                       and record.state is ExecutionState.TERMINATION_CONFIRMED)
        new_output = ((record.stdout and record.stdout != host.stdout)
                      or (record.stderr and record.stderr != host.stderr))
        if same_state and not new_output:
            return
        if is_terminal(host.state) and not can_confirm and not new_output:
            return
        if record.state in {ExecutionState.EXITED, ExecutionState.FAILED} or new_output:
            await self.event_store.append_execution_event(
                record.execution_id, 'gateway_result', f'网关回读终态 {record.state.value}',
                state=None if same_state else record.state,
                returncode=record.returncode, error=record.error,
                stdout=record.stdout, stderr=record.stderr,
                stdout_truncated=record.stdout_truncated, stderr_truncated=record.stderr_truncated)
            return
        if host.state not in {ExecutionState.CANCEL_REQUESTED, ExecutionState.TERMINATION_UNCONFIRMED}:
            await self.event_store.append_execution_event(
                record.execution_id, 'gateway_result', '网关记录了停止请求',
                state=ExecutionState.CANCEL_REQUESTED)
        await self.event_store.append_execution_event(
            record.execution_id, 'gateway_result', f'网关回读终止结果 {record.state.value}',
            state=record.state, termination=record.termination, error=record.error,
            stdout=record.stdout or None, stderr=record.stderr or None)

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
                    if host.state is ExecutionState.ACCEPTED and host.started_at is None:
                        # The host row was written but the Gateway never stored
                        # this id; there is no start fact, so this id did not
                        # occupy a container.  Anything past ACCEPTED is not
                        # that case.
                        await self._mark_unrecorded(
                            host.execution_id, f'网关日志中没有该执行（HTTP 404）：{error.detail}')
                        continue
                    await self._mark_unresolved(
                        host.execution_id,
                        f'网关日志当前没有该执行（HTTP 404）：{error.detail}；不证明历史上从未运行')
                    return 'occupied', (f'工作区 {workspace_id} 的执行 {host.execution_id} '
                                        '在网关日志中找不到，占用不能解除；本次不开始新执行')
                await self._mark_unresolved(
                    host.execution_id, f'网关拒绝了查询（HTTP {error.status_code}）：{error.detail}')
                return 'unknown', (f'网关未接受对工作区 {workspace_id} 的核对'
                                   f'（HTTP {error.status_code}）：{error.detail}；本次不开始新执行')
            await self._mirror_terminal(record)
            if record.state in OCCUPYING_STATES:
                blocked, blocking = True, record.execution_id
        if blocked:
            return 'occupied', (f'工作区 {workspace_id} 的执行 {blocking} 仍占用，不能新用')
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
    async def _artifacts_summary(self, execution_id: str) -> tuple[list[dict], bool, bool]:
        """This execution's registered outputs, whether it was cut, and whether
        the read itself succeeded.

        An unreachable Gateway is "not readable yet", not an empty directory:
        the two are returned differently so a caller cannot report a failed
        listing as a work that produced nothing.  A listing that *was* read is
        known; if it was cut at the deployment's artifact cap, the flag says so
        rather than the read being reported as unknown.
        """
        try:
            listing = await self.client.artifacts(execution_id)
        except (GatewayRefused, GatewayUnavailable, GatewayResultUnknown) as error:
            raise RuntimeError(f'网关暂时不可用，产物清单暂不可读：{error}') from None
        return ([{'path': item['path'], 'size_bytes': item['size_bytes'],
                  'media_type': item['media_type'], 'artifact_id': item['artifact_id'],
                  'over_limit': False} for item in listing.get('artifacts', [])],
                bool(listing.get('truncated')), True)

    async def _listing_for(self, execution_id: str) -> dict | None:
        """One execution's listing, or None when the Gateway has no such log."""
        try:
            return await self.client.artifacts(execution_id)
        except GatewayRefused as error:
            if error.status_code == 404:
                return None
            raise RuntimeError(f'网关拒绝读取产物清单：{error}') from None
        except (GatewayUnavailable, GatewayResultUnknown) as error:
            raise RuntimeError(f'网关暂时不可用，产物暂不可读：{error}') from None

    async def _snapshot_execution(self, scene_id: str, job_id: str):
        """The execution whose registered outputs are this job's current files.

        The current Python workspace is a *confirmed* snapshot, not a later
        browser or media execution belonging to the same job. A row that was refused, or that is
        still running or unknown, has no confirmed outputs yet, so the current
        listing must not fall back to an older execution's files and must not
        report "no files" either — it reports that the snapshot is not
        readable yet.
        """
        records = [record for record in await self.event_store.executions_for_job(scene_id, job_id)
                   if record.worker_type == 'python']
        if not records:
            return None, None
        newest = records[-1]
        if newest.state in OCCUPYING_STATES:
            raise RuntimeError(
                f'当前执行 {newest.execution_id} 仍占用工作区（{newest.state.value}），'
                '产物快照尚未确认；历史文件须显式指定 execution_id')
        if newest.state not in {ExecutionState.EXITED, ExecutionState.FAILED,
                                ExecutionState.TERMINATION_CONFIRMED}:
            raise RuntimeError(
                f'当前执行 {newest.execution_id} 的结局 {newest.state.value} 不是已确认快照')
        listing = await self._listing_for(newest.execution_id)
        if listing is None:
            raise RuntimeError(
                f'当前执行 {newest.execution_id} 已结束，但产物清单暂不可读')
        return newest, listing

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
            if host.worker_type != 'python':
                raise ValueError('工作空间文件只能读取本工作的 Python 执行产物；浏览器和媒体资料沿各自观察读取')
            if host.state in OCCUPYING_STATES:
                raise RuntimeError(f'指定执行 {execution_id} 的产物快照尚未确认（{host.state.value}）')
            listing = await self._listing_for(execution_id)
            if listing is None:
                raise ValueError('指定的执行不在网关日志中，其产物身份无法确认')
            for item in listing.get('artifacts', []):
                if item.get('path') == path:
                    return {**item, 'execution_id': execution_id}
            return None
        host, listing = await self._snapshot_execution(scene_id, job_id)
        if listing is None:
            raise ValueError('该工作还没有已确认的执行快照，无法读取当前产物')
        for item in listing.get('artifacts', []):
            if item.get('path') == path:
                return {**item, 'execution_id': host.execution_id if host else None}
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
        skipped = 0
        page: list[str] = []
        page_len = 0
        more = False
        async for chunk in self.client.artifact_chunks(artifact['artifact_id']):
            text = decoder.decode(chunk, final=False)
            if skipped < offset:
                take = min(len(text), offset - skipped)
                skipped += take
                text = text[take:]
            if not text:
                continue
            if page_len >= limit:
                more = True
                break
            need = limit - page_len
            page.append(text[:need])
            page_len += min(len(text), need)
            if len(text) > need:
                more = True
                break
        else:
            tail = decoder.decode(b'', final=True)
            if skipped < offset:
                take = min(len(tail), offset - skipped)
                skipped += take
                tail = tail[take:]
            if tail and page_len < limit:
                need = limit - page_len
                page.append(tail[:need])
                page_len += min(len(tail), need)
                if len(tail) > need:
                    more = True
            elif tail:
                more = True
        piece = ''.join(page)
        reach = offset + page_len if more else None
        return {'scene_id': scene_id, 'job_id': job_id, 'path': path, 'content': piece,
                'offset': offset, 'next_offset': reach, 'truncated': reach is not None,
                'execution_id': execution_id or artifact.get('execution_id')}

    # ---- tool entries --------------------------------------------------------
    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        _host, listing = await self._snapshot_execution(scope.scene_id, scope.job_id)
        if listing is None:
            raise RuntimeError('该工作还没有已确认的执行快照，文件清单暂不可读')
        return {'workspace_id': scope.workspace_id, 'scene_id': scope.scene_id, 'job_id': scope.job_id,
                'execution_id': _host.execution_id if _host else None,
                'files': sorted(item['path'] for item in listing.get('artifacts', [])),
                'truncated': bool(listing.get('truncated')),
                'truncated_reason': 'artifact_cap' if listing.get('truncated') else None}

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        return await self._artifact_text(scope.scene_id, scope.job_id, request.path,
                                         request.offset, request.limit,
                                         execution_id=request.execution_id)

    async def export_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
        row = await self._artifact_by_path(scope.scene_id, scope.job_id, request.path,
                                           execution_id=request.execution_id)
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
        if job is None:
            return None
        panel_call = type('PanelCall', (), {'role': 'work', 'job_id': job_id,
            'requester_qq_uid': job['requester_qq_uid'], 'scene_id': scene_id})()
        return await self.scope_for(panel_call, allow_terminal=True)

    async def read_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int,
                           execution_id: str | None = None) -> dict | None:
        scope = await self._panel_scope(scene_id, job_id)
        if scope is None:
            return None
        return await self._artifact_text(scene_id, job_id, path, offset, limit,
                                         execution_id=execution_id)

    async def read_bytes_for_job(self, scene_id: str, job_id: str, path: str,
                                 execution_id: str | None = None) -> tuple[bytes, str] | None:
        scope = await self._panel_scope(scene_id, job_id)
        if scope is None:
            return None
        row = await self._artifact_by_path(scene_id, job_id, path, execution_id=execution_id)
        if row is None:
            raise ValueError('工作空间文件不存在或尚未登记为产物')
        buffer = bytearray()
        async for chunk in self.client.artifact_chunks(row['artifact_id']):
            buffer.extend(chunk)
        data = bytes(buffer)
        return data, row['media_type'] or 'application/octet-stream'

    async def list_for_job(self, scene_id: str, job_id: str) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None:
            return None
        host, listing = await self._snapshot_execution(scene_id, job_id)
        if listing is None:
            # No confirmed snapshot yet: an honest "not readable", never an
            # empty directory that reads as "this work produced nothing".
            raise RuntimeError('该工作还没有已确认的执行快照，产物清单暂不可读')
        artifacts = [{**WorkspaceArtifact(path=item['path'], size_bytes=item['size_bytes'],
                                          media_type=item['media_type']).model_dump(mode='json'),
                      'execution_id': host.execution_id if host else None,
                      'artifact_id': item.get('artifact_id')}
                     for item in listing.get('artifacts', [])]
        return {'scene_id': scene_id, 'job_id': job_id,
                'execution_id': host.execution_id if host else None,
                'job_revision': host.job_revision if host else None,
                'snapshot_kind': 'execution_artifacts',
                'sampled_at': self.event_store.clock(),
                'artifacts': artifacts,
                'truncated': bool(listing.get('truncated')),
                'truncated_reason': 'artifact_cap' if listing.get('truncated') else None}
