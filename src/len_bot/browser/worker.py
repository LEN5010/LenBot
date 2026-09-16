from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    allowed_hosts: list[str] = Field(default_factory=list, title="允许访问的域名",
        description="只读白名单，逐个域名填写；空列表表示不访问任何站点")
    headless: bool = Field(default=True, title="无界面运行",
        description="保持开启；这是运行参数，不是隔离证明")
    timeout_seconds: float = Field(default=20.0, gt=0, le=120, title="单次读取超时（秒）",
        description="单次页面读取的期限，不超过 120 秒")
    max_text_chars: int = Field(default=12000, ge=100, le=100000, title="正文保留字符数",
        description="每次呈现的页面正文字符数，100—100000；续读使用固定观察")
    max_snapshot_chars: int = Field(default=1_000_000, ge=1000, le=2_000_000, title='单次 DOM 采集字符上限',
        description='采集阶段即限制正文；达到上限标记部分观察，不先搬回无限长 innerText')
    max_open_pages: int = Field(default=4, ge=1, le=8, title="同一工作同时打开的页面数",
        description="同工作可保留多个 page_ref 的上限；超出须先关闭或结束工作")
    allow_interactions: bool = Field(default=False, title="允许页面交互",
        description="默认关闭；开启后仍只在获准的功能范围内操作，不替代隔离验收")


class UrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=4000)


class InteractionRequest(UrlRequest):
    action: str = Field(pattern=r"^(scroll|click)$")
    selector: str | None = Field(default=None, max_length=300)


class BrowserWorker:
    def __init__(self, config: BrowserConfig):
        self.config = config
        self._playwright = None
        self._browser = None

    def validate_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("browser URL must be an absolute HTTP(S) URL")
        if parsed.hostname not in self.config.allowed_hosts:
            raise ValueError("browser URL host is not in the configured allowlist")

    async def page(self, url: str):
        self.validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("Playwright is not installed for the browser worker") from None
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.config.headless)
        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.config.timeout_seconds * 1000))
        except Exception:
            await page.close()
            raise
        return page

    async def snapshot(self, request: UrlRequest) -> dict:
        page = await self.page(request.url)
        try:
            return {"url": page.url, "title": await page.title(),
                    "text": (await page.locator("body").inner_text())[:self.config.max_text_chars],
                    "coverage": "browser_dom_text", "pixels_loaded": False}
        finally:
            await page.close()

    async def capture(self, request: UrlRequest) -> bytes:
        page = await self.page(request.url)
        try:
            return await page.screenshot(type="png", full_page=False)
        finally:
            await page.close()

    async def interact(self, request: InteractionRequest) -> dict:
        if not self.config.allow_interactions:
            raise PermissionError("browser interactions are disabled by configuration")
        if request.action == "click" and not request.selector:
            raise ValueError("click interaction requires a selector")
        page = await self.page(request.url)
        try:
            if request.action == "scroll":
                await page.mouse.wheel(0, 700)
            elif request.selector:
                await page.locator(request.selector).click()
            return {"url": page.url, "title": await page.title(), "action": request.action,
                    "text": (await page.locator("body").inner_text())[:self.config.max_text_chars]}
        finally:
            await page.close()

    async def close(self):
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
