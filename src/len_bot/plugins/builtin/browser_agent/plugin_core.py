from __future__ import annotations

import json
import asyncio

from len_bot.browser.models import BrowserCaptureInput, BrowserInteractInput, BrowserOpenInput, BrowserPageInput
from len_bot.browser.worker_v2 import BrowserWorkerV2
from len_bot.execution.models import WorkspaceScope
from len_bot.execution.admission import require_execution_job
from len_bot.execution.client import WorkerGatewayConfig
from len_bot.browser.gateway_service import GatewayBrowserService
from len_bot.plugins.api import BasePlugin, PluginCallContext, PluginContext, ToolResult, ToolSource

from .config import BrowserPluginConfig


class BrowserAgentPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.context = context
        self.config: BrowserPluginConfig = context.config
        workspace = context._runtime.config_store.current.plugins.get('workspace')
        gateway = (getattr(workspace, 'config', None) or {}).get('gateway')
        self.gateway = GatewayBrowserService(context, self.config, WorkerGatewayConfig.model_validate(gateway)) if gateway else None
        self.worker = None if self.gateway else BrowserWorkerV2(self.config.browser)
        self._local_scopes = {}

    async def on_load(self, context: PluginContext):
        context.register_tool('browser_open', '在当前信息工作中打开白名单公开网页并返回可见 DOM 文本与临时元素引用。',
            BrowserOpenInput, self.open, purpose='观察公开网页', aliases=('打开网页', '看看网页'),
            keywords=('网页', '浏览器', '网站'), kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_snapshot', '读取当前工作浏览器页的 DOM 文本和元素引用；可用 text_offset 接续读取长正文。',
            BrowserPageInput, self.snapshot, purpose='读取网页状态', aliases=('网页状态',), keywords=('网页', '浏览器', '页面'),
            kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_interact', '按配置执行带观察版本校验的滚动或元素点击，默认关闭。',
            BrowserInteractInput, self.interact, purpose='操作受控网页', aliases=('操作网页',), keywords=('网页', '点击', '滚动'),
            kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_capture', '读取当前工作浏览器页的视图截图并登记场景资产。',
            BrowserCaptureInput, self.capture, purpose='读取网页像素', aliases=('网页截图',), keywords=('网页', '截图', '浏览器'),
            kind='read', roles=('work',), deferred=True)

    async def on_unload(self):
        await (self.gateway or self.worker).close()

    async def close_job(self, job: dict):
        if self.gateway:
            await self.gateway.close_job(job)
            return
        requester = job.get('requester_qq_uid')
        if requester:
            scope = WorkspaceScope(scene_id=job['scene_id'], requester_qq_uid=requester, job_id=job['id'])
            for key in self._local_scopes.pop(scope.workspace_id, set()):
                await self.worker.close_scope(key)

    def _refuse_host_when_gateway(self):
        workspace = self.context._runtime.config_store.current.plugins.get('workspace')
        config = getattr(workspace, 'config', None) or {}
        if isinstance(config, dict) and config.get('gateway'):
            raise RuntimeError('Gateway 已选为执行后端，宿主浏览器入口已关闭；请重新加载浏览器插件采用独立 worker')

    async def _execute(self, operation, values, call):
        if self.gateway:
            workspace = self.context._runtime.config_store.current.plugins.get('workspace')
            gateway = (getattr(workspace, 'config', None) or {}).get('gateway')
            if gateway is None or WorkerGatewayConfig.model_validate(gateway) != self.gateway.gateway_config:
                raise ValueError('执行后端配置已变化，请重新加载浏览器插件')
            return await self.gateway.execute(operation, values, call)
        scope = await self._scope(call)
        key = f'{scope.workspace_id}:r{call.job_revision}'
        self._local_scopes.setdefault(scope.workspace_id, set()).add(key)
        async def admit():
            self._refuse_host_when_gateway()
            return await require_execution_job(self.context.event_store, call, self.manifest.id)
        job = await admit()
        deadline = (job.get('budget') or {}).get('deadline_at')
        remaining = None if deadline is None else max(0, deadline - self.context.now())
        async with asyncio.timeout(remaining):
            return await getattr(self.worker, operation)(key, values, admission=admit)

    async def _scope(self, call):
        self._refuse_host_when_gateway()
        if call.role != 'work' or not call.job_id or not call.requester_qq_uid:
            raise ValueError('浏览器只允许在已有 work 工作中使用')
        await require_execution_job(self.context.event_store, call, self.manifest.id)
        return WorkspaceScope(scene_id=call.scene_id, requester_qq_uid=call.requester_qq_uid, job_id=call.job_id)

    async def open(self, values: BrowserOpenInput, call: PluginCallContext):
        try:
            value = await self._execute('open', values, call)
            return self._result(value, values.url)
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def snapshot(self, values: BrowserPageInput, call: PluginCallContext):
        try:
            value = await self._execute('snapshot', values, call)
            return self._result(value, value['url'])
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def interact(self, values: BrowserInteractInput, call: PluginCallContext):
        try:
            value = await self._execute('interact', values, call)
            return self._result(value, value['url'])
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def capture(self, values: BrowserCaptureInput, call: PluginCallContext):
        try:
            png = await self._execute('capture', values, call)
            asset_id = await call.save_image(png, '受控浏览器网页截图')
            return ToolResult(status='ok', coverage='browser_pixels', attachments=[asset_id],
                content=json.dumps({'page_ref': values.page_ref, 'asset_id': asset_id,
                    'area': values.area, 'asset_registered': True, 'pixels_loaded': False},
                    ensure_ascii=False), evidence_kind='external')
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    @staticmethod
    def _result(value: dict, url: str):
        return ToolResult(status='ok', coverage=value.pop('coverage', 'browser_dom_text'),
            content=json.dumps(value, ensure_ascii=False), evidence_kind='external',
            sources=[ToolSource(url=url, title='受控浏览器公开页面')])
