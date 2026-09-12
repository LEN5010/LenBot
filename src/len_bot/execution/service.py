"""Host-owned workspace admission, input export and artifact checks."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from len_bot.execution.models import RunPythonInput, WorkspaceArtifact, WorkspaceFileInput, WorkspaceScope
from len_bot.execution.workspace import FileRequest, RunPythonRequest, WorkspaceRequest, WorkspaceWorker


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
            size = path.stat().st_size
            if size > self.worker.config.max_artifact_bytes:
                continue
            artifacts.append(WorkspaceArtifact(path=relative, size_bytes=size,
                media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'))
        return artifacts[:self.worker.config.max_artifact_files]

    async def run_python(self, call, request: RunPythonInput) -> dict:
        scope = await self.scope_for(call)
        async with self.worker.run_lock(scope.workspace_id):
            manifest = await self._export_inputs(scope, request.input_result_ids, call.scene_id)
            result = await self.worker._run_python_locked(RunPythonRequest(workspace_id=scope.workspace_id, script=request.script))
        result['workspace'] = {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'input_manifest': manifest, 'artifacts': [item.model_dump(mode='json') for item in self._artifacts(scope)]}
        return result

    async def list_files(self, call) -> dict:
        scope = await self.scope_for(call)
        result = self.worker.list_files(WorkspaceRequest(workspace_id=scope.workspace_id))
        result.update({'scene_id': scope.scene_id, 'job_id': scope.job_id})
        return result

    async def read_file(self, call, request: WorkspaceFileInput) -> dict:
        scope = await self.scope_for(call)
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
        path = self.worker.file_path(scope.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('工作空间文件不存在或不是普通文件')
        size = path.stat().st_size
        if size > self.worker.config.max_artifact_bytes:
            raise ValueError('产物超过配置的字节上限')
        artifact = WorkspaceArtifact(path=request.path, size_bytes=size,
            media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        attachments = []
        if artifact.media_type.startswith('image/'):
            try:
                asset_id = await call.save_image(self.worker.read_bytes(
                    FileRequest(workspace_id=scope.workspace_id, path=request.path)),
                    f'工作空间图片产物：{request.path}')
                artifact = artifact.model_copy(update={'result_id': asset_id})
                attachments.append(asset_id)
            except (ValueError, OSError):
                # The panel download remains available when the image is not a
                # supported media asset for the normal send pipeline.
                pass
        return {'scene_id': scope.scene_id, 'job_id': scope.job_id,
            'artifact': artifact.model_dump(mode='json'),
            'attachments': attachments,
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
        data = self.worker.read_bytes(FileRequest(workspace_id=scope.workspace_id, path=path))
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        return data, mime

    async def list_for_job(self, scene_id: str, job_id: str) -> dict | None:
        job = await self.event_store.get_job(job_id, scene_id)
        if job is None or not job.get('requester_qq_uid'):
            return None
        scope = WorkspaceScope(scene_id=scene_id, requester_qq_uid=job['requester_qq_uid'], job_id=job_id)
        return {'scene_id': scene_id, 'job_id': job_id,
            'artifacts': [item.model_dump(mode='json') for item in self._artifacts(scope)]}
