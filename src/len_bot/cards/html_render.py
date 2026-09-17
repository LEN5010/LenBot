"""Render our own trusted card HTML to PNG: no navigation, no network.

The Gateway browser session is job-scoped and egress-authorized because it
visits pages we do not control (`browser/gateway_service.py` requires a
tool_call_id and an admitted execution job).  A push card is the opposite
case: our own template, every image already inlined as a data: URI, and a
background poller that has no job to bind to.  Routing it through that
boundary would mean inventing a job for it, so this renders directly -- the
same in-process Playwright the browser plugin already falls back to when no
Gateway is configured.

`set_content` never navigates, and http/https requests are aborted outright,
so "the card renders offline" is enforced here rather than merely intended.
"""
from __future__ import annotations

import asyncio

CARD_VIEWPORT_WIDTH = 1200
MAX_CARD_BYTES = 4 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 30.0


def fill_template(template: str, context: dict) -> str:
    """Jinja2 with autoescape off, matching the card template's contract.

    The template marks every field either `| e` or `| safe`: the context
    builder escapes the `| safe` ones in Python before they get here.  Turning
    autoescape on would double-escape them, so the escaping stays where the
    template expects it.
    """
    from jinja2 import Environment

    return Environment(autoescape=False).from_string(template).render(**context)


class HtmlCardRenderer:
    """One lazily started headless browser shared by every card render."""

    def __init__(self, *, viewport_width: int = CARD_VIEWPORT_WIDTH,
                 timeout_seconds: float = RENDER_TIMEOUT_SECONDS):
        self.viewport_width = viewport_width
        self.timeout_seconds = timeout_seconds
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=True, chromium_sandbox=True)
        except BaseException:
            await asyncio.shield(self._playwright.stop())
            self._playwright = None
            raise
        return self._browser

    async def warmup(self):
        """Start Chromium now so the first card is not a cold launch."""
        async with self._lock:
            await self._ensure_browser()

    async def render(self, html: str) -> bytes:
        """One card's HTML in, one PNG out."""
        async with self._lock:
            browser = await self._ensure_browser()
            page = await browser.new_page(
                viewport={'width': self.viewport_width, 'height': 800})
            try:
                # The card is self-contained. Anything still reaching for the
                # network is a missing data: URI, and a blank region is a
                # better answer than a render that hangs on a CDN.
                await page.route('http://**', lambda route: asyncio.ensure_future(route.abort()))
                await page.route('https://**', lambda route: asyncio.ensure_future(route.abort()))
                await page.set_content(html, wait_until='load',
                                       timeout=int(self.timeout_seconds * 1000))
                png = await page.screenshot(type='png', full_page=True,
                                            omit_background=True,
                                            animations='disabled', scale='css')
            finally:
                await page.close()
        if len(png) > MAX_CARD_BYTES:
            raise ValueError(f'卡片截图 {len(png)} 字节，超过 {MAX_CARD_BYTES} 上限')
        return png

    async def render_template(self, template: str, context: dict) -> bytes:
        return await self.render(fill_template(template, context))

    async def close(self):
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
