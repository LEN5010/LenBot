from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    allowed_hosts: list[str] = Field(default_factory=list)
    headless: bool = True
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_text_chars: int = Field(default=12000, ge=100, le=100000)
    allow_interactions: bool = False


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
