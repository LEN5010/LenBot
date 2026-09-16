from __future__ import annotations

import json

from len_bot.execution.client import WorkerGatewayClient
from len_bot.execution.models import ListWorkspaceInput, RunPythonInput, WorkspaceFileInput
from len_bot.execution.service import GatewayWorkspaceService, WorkspaceService
from len_bot.execution.workspace import WorkspaceWorker
from len_bot.plugins.api import BasePlugin, PluginCallContext, PluginContext, ToolResult

from .config import WorkspacePluginConfig


def build_workspace_service(config: WorkspacePluginConfig, data_directory, event_store, plugin_id: str,
                            media_service=None):
    """The one execution and read service the configured backend selects.

    Every caller — the plugin's tools and the panel's read-only artifact
    entries — comes through here, so a backend is chosen in exactly one place
    and a gateway-only configuration never constructs a local worker.

    ``media_service`` is this deployment's scoped media reader.  It is what an
    authorized attachment import reads real bytes through; a caller that has
    none (the panel's read-only backend) can still list and read artifacts,
    and its attachment imports fail loudly instead of exporting less than the
    caller asked for.
    """
    if config.gateway is not None:
        # The gateway is the one backend when configured; the host keeps
        # no container runtime and never falls back to a local run.
        return GatewayWorkspaceService(
            WorkerGatewayClient(config.gateway), config.gateway, event_store, plugin_id,
            media_service)
    return WorkspaceService(WorkspaceWorker(config.worker, data_directory), event_store, plugin_id,
                            media_service)


class WorkspacePlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: WorkspacePluginConfig = context.config
        self.service = build_workspace_service(
            self.config, context.data_directory, context.event_store, self.manifest.id,
            context.media_service)
        if isinstance(self.service, GatewayWorkspaceService):
            self.service.action_reviewer = context._runtime.action_reviewer

    async def on_load(self, context: PluginContext):
        context.register_tool('run_python', '在当前信息工作的离线 Python 容器中处理已获准资料；每次调用是新进程，文件可持续。输入清单位于只读的 /lenbot-control/manifest.json，输入文件在与它同级的 input/ 下；产物写入当前目录 /workspace；依赖由已配置镜像提供。可导入本工作已保存的文本资料（input_result_ids）与本工作来源里已登记的图片（input_asset_ids）；每个文件的来源身份记在清单的 inputs 里，面板与发送都不会因导入而被触发。',
            RunPythonInput, self.run_python, purpose='执行隔离 Python 处理', aliases=('运行Python', 'Python处理'),
            keywords=('Python', '代码', '脚本', '表格', '图表'), kind='read', roles=('work',), deferred=True)
        context.register_tool('list_workspace_files', '列出当前信息工作归属的相对文件，不浏览宿主目录。',
            ListWorkspaceInput, self.list_files,
            purpose='查看工作区文件', aliases=('列出文件',), keywords=('工作区', '文件'), kind='read', roles=('work',))
        context.register_tool('read_workspace_file', '分页读取当前信息工作中的普通文件。', WorkspaceFileInput, self.read_file,
            purpose='读取工作区文件', aliases=('读取文件',), keywords=('工作区', '文件', '读取'), kind='read', roles=('work',))
        context.register_tool('export_workspace_artifact', '导出当前工作的文件产物。支持的图片登记为 attachments 中的场景媒体引用，普通文件可在授权面板下载；不自动发送。',
            WorkspaceFileInput, self.export_file, purpose='导出工作区产物', aliases=('导出文件',),
            keywords=('工作区', '文件', '导出', '产物'), kind='read', roles=('work',))

    async def on_unload(self):
        service = getattr(self, 'service', None)
        client = getattr(service, 'client', None)
        if client is not None:
            await client.close()

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

    async def artifact_for_job(self, scene_id: str, job_id: str, path: str, offset: int, limit: int,
                               execution_id: str | None = None):
        return await self.service.read_for_job(scene_id, job_id, path, offset, limit,
                                               execution_id=execution_id)

    async def artifacts_for_job(self, scene_id: str, job_id: str):
        return await self.service.list_for_job(scene_id, job_id)

    async def artifact_bytes_for_job(self, scene_id: str, job_id: str, path: str,
                                     execution_id: str | None = None):
        return await self.service.read_bytes_for_job(scene_id, job_id, path,
                                                     execution_id=execution_id)
