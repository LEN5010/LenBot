from __future__ import annotations

import json

from len_bot.execution.models import ListWorkspaceInput, RunPythonInput, WorkspaceFileInput
from len_bot.execution.service import WorkspaceService
from len_bot.execution.workspace import WorkspaceWorker
from len_bot.plugins.api import BasePlugin, PluginCallContext, PluginContext, ToolResult

from .config import WorkspacePluginConfig


class WorkspacePlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: WorkspacePluginConfig = context.config
        self.service = WorkspaceService(WorkspaceWorker(self.config.worker, context.data_directory),
            context.event_store, self.manifest.id)

    async def on_load(self, context: PluginContext):
        context.register_tool('run_python', '在当前信息工作的隔离离线 Python 容器中处理已获准的资料；每次调用是新进程，文件可持续。',
            RunPythonInput, self.run_python, purpose='执行隔离 Python 处理', aliases=('运行Python', 'Python处理'),
            keywords=('Python', '代码', '脚本', '表格', '图表'), kind='read', roles=('work',), deferred=True)
        context.register_tool('list_workspace_files', '列出当前信息工作归属的相对文件，不浏览宿主目录。',
            ListWorkspaceInput, self.list_files,
            purpose='查看工作区文件', aliases=('列出文件',), keywords=('工作区', '文件'), kind='read', roles=('work',))
        context.register_tool('read_workspace_file', '分页读取当前信息工作中的普通文件。', WorkspaceFileInput, self.read_file,
            purpose='读取工作区文件', aliases=('读取文件',), keywords=('工作区', '文件', '读取'), kind='read', roles=('work',))
        context.register_tool('export_workspace_artifact', '登记一个当前工作的普通文件产物供授权面板读取；不自动发送。',
            WorkspaceFileInput, self.export_file, purpose='导出工作区产物', aliases=('导出文件',),
            keywords=('工作区', '文件', '导出', '产物'), kind='read', roles=('work',))

    async def run_python(self, values: RunPythonInput, call: PluginCallContext):
        return await self._run(lambda: self.service.run_python(call, values))

    async def list_files(self, values: WorkspaceFileInput, call: PluginCallContext):
        return await self._run(lambda: self.service.list_files(call))

    async def read_file(self, values: WorkspaceFileInput, call: PluginCallContext):
        return await self._run(lambda: self.service.read_file(call, values))

    async def export_file(self, values: WorkspaceFileInput, call: PluginCallContext):
        return await self._run(lambda: self.service.export_file(call, values))

    async def _run(self, operation):
        try:
            value = await operation()
        except ValueError as error:
            return ToolResult.failure(str(error), 'workspace_scope_or_path')
        except (OSError, RuntimeError) as error:
            return ToolResult.failure(str(error), 'workspace_execution_failed')
        status = value.pop('status', 'ok')
        attachments = value.pop('attachments', [])
        if status == 'unsupported':
            return ToolResult(status='unsupported', error_code='worker_unavailable', coverage='workspace', attachments=attachments,
                content=json.dumps(value, ensure_ascii=False), evidence_kind='retrieval')
        return ToolResult(status=status, coverage='workspace', attachments=attachments,
            content=json.dumps(value, ensure_ascii=False), evidence_kind='retrieval')

    async def artifact_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int):
        return await self.service.read_for_job(scene_id, job_id, path, offset, limit)

    async def artifacts_for_job(self, scene_id: str, job_id: str):
        return await self.service.list_for_job(scene_id, job_id)
