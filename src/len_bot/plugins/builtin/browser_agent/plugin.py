from __future__ import annotations

import json

from len_bot.browser.worker import BrowserWorker, InteractionRequest, UrlRequest
from len_bot.plugins.api import BasePlugin, PluginCallContext, PluginContext, ToolResult

from .config import BrowserPluginConfig


class BrowserAgentPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: BrowserPluginConfig = context.config
        self.worker = BrowserWorker(self.config.browser)

    async def on_load(self, context: PluginContext):
        context.register_tool("browser_open", "打开白名单网页并读取可见文本；不登录、不提交表单。", UrlRequest,
            self.open, purpose="观察网页", aliases=("打开网页", "看看网页"), keywords=("网页", "浏览器", "网站"),
            kind="read", roles=("conversation", "work"), deferred=True)
        context.register_tool("browser_capture", "读取白名单网页当前视图的像素截图并登记为场景资产。", UrlRequest,
            self.capture, purpose="读取网页截图", aliases=("网页截图",), keywords=("网页", "截图", "浏览器"),
            kind="read", roles=("conversation", "work"), deferred=True)
        context.register_tool("browser_interact", "按配置允许的安全动作观察网页；默认关闭交互。", InteractionRequest,
            self.interact, purpose="操作受控网页", aliases=("操作网页",), keywords=("网页", "点击", "滚动"),
            kind="read", roles=("conversation", "work"), deferred=True)

    async def on_unload(self):
        await self.worker.close()

    async def open(self, values: UrlRequest, call: PluginCallContext):
        try:
            return self._result(await self.worker.snapshot(values))
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), "browser_unavailable")

    async def capture(self, values: UrlRequest, call: PluginCallContext):
        try:
            asset = await call.save_image(await self.worker.capture(values), "受控浏览器网页截图")
            return ToolResult(status="ok", coverage="browser_pixels", attachments=[asset],
                content=json.dumps({"url": values.url, "asset_id": asset, "pixels_loaded": True}, ensure_ascii=False),
                evidence_kind="external")
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), "browser_unavailable")

    async def interact(self, values: InteractionRequest, call: PluginCallContext):
        try:
            return self._result(await self.worker.interact(values))
        except (ValueError, RuntimeError, PermissionError) as error:
            return ToolResult.failure(str(error), "browser_unavailable")

    @staticmethod
    def _result(value):
        return ToolResult(status="ok", coverage=value.pop("coverage", "browser_dom_text"),
            content=json.dumps(value, ensure_ascii=False), evidence_kind="external")
