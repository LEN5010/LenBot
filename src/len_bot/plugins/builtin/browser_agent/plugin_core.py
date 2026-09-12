from __future__ import annotations

import json

from len_bot.browser.models import BrowserCaptureInput, BrowserInteractInput, BrowserOpenInput, BrowserPageInput
from len_bot.browser.worker_v2 import BrowserWorkerV2
from len_bot.execution.models import WorkspaceScope
from len_bot.plugins.api import BasePlugin, PluginCallContext, PluginContext, ToolResult, ToolSource

from .config import BrowserPluginConfig


class BrowserAgentPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.context = context
        self.config: BrowserPluginConfig = context.config
        self.worker = BrowserWorkerV2(self.config.browser)

    async def on_load(self, context: PluginContext):
        context.register_tool('browser_open', '在当前信息工作中打开白名单公开网页并返回可见 DOM 文本与临时元素引用。',
            BrowserOpenInput, self.open, purpose='观察公开网页', aliases=('打开网页', '看看网页'),
            keywords=('网页', '浏览器', '网站'), kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_snapshot', '读取当前工作浏览器页的最新可见 DOM 文本和元素引用。',
            BrowserPageInput, self.snapshot, purpose='读取网页状态', aliases=('网页状态',), keywords=('网页', '浏览器', '页面'),
            kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_interact', '按配置执行带观察版本校验的滚动或元素点击，默认关闭。',
            BrowserInteractInput, self.interact, purpose='操作受控网页', aliases=('操作网页',), keywords=('网页', '点击', '滚动'),
            kind='read', roles=('work',), deferred=True)
        context.register_tool('browser_capture', '读取当前工作浏览器页的视图截图并登记场景资产。',
            BrowserCaptureInput, self.capture, purpose='读取网页像素', aliases=('网页截图',), keywords=('网页', '截图', '浏览器'),
            kind='read', roles=('work',), deferred=True)

    async def on_unload(self):
        await self.worker.close()

    async def close_job(self, job: dict):
        requester = job.get('requester_qq_uid')
        if requester:
            scope = WorkspaceScope(scene_id=job['scene_id'], requester_qq_uid=requester, job_id=job['id'])
            await self.worker.close_scope(scope.workspace_id)

    async def _scope(self, call):
        if call.role != 'work' or not call.job_id or not call.requester_qq_uid:
            raise ValueError('浏览器只允许在已有 work 工作中使用')
        job = await self.context.event_store.get_job(call.job_id, call.scene_id)
        if not job or job.get('requester_qq_uid') != call.requester_qq_uid:
            raise ValueError('浏览器归属与当前工作不一致')
        if job['status'] not in {'pending', 'claimed', 'processing', 'result_ready'}:
            scope = WorkspaceScope(scene_id=call.scene_id, requester_qq_uid=call.requester_qq_uid, job_id=call.job_id)
            await self.worker.close_scope(scope.workspace_id)
            raise ValueError('当前工作已结束，浏览器页面已关闭')
        return WorkspaceScope(scene_id=call.scene_id, requester_qq_uid=call.requester_qq_uid, job_id=call.job_id)

    async def open(self, values: BrowserOpenInput, call: PluginCallContext):
        try:
            scope = await self._scope(call)
            value = await self.worker.open(scope.workspace_id, values)
            return self._result(value, values.url)
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def snapshot(self, values: BrowserPageInput, call: PluginCallContext):
        try:
            scope = await self._scope(call)
            value = await self.worker.snapshot(scope.workspace_id, values)
            return self._result(value, value['url'])
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def interact(self, values: BrowserInteractInput, call: PluginCallContext):
        try:
            scope = await self._scope(call)
            value = await self.worker.interact(scope.workspace_id, values)
            return self._result(value, value['url'])
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    async def capture(self, values: BrowserCaptureInput, call: PluginCallContext):
        try:
            scope = await self._scope(call)
            png = await self.worker.capture(scope.workspace_id, values)
            asset_id = await call.save_image(png, '受控浏览器网页截图')
            return ToolResult(status='ok', coverage='browser_pixels', attachments=[asset_id],
                content=json.dumps({'page_ref': values.page_ref, 'asset_id': asset_id,
                    'area': values.area, 'pixels_loaded': True}, ensure_ascii=False), evidence_kind='external')
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), 'browser_unavailable')

    @staticmethod
    def _result(value: dict, url: str):
        return ToolResult(status='ok', coverage=value.pop('coverage', 'browser_dom_text'),
            content=json.dumps(value, ensure_ascii=False), evidence_kind='external',
            sources=[ToolSource(url=url, title='受控浏览器公开页面')])
