"""Task-scoped Playwright observation worker with revisioned element refs."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import BrowserCaptureInput, BrowserInteractInput, BrowserOpenInput, BrowserPageInput
from .worker import BrowserConfig


@dataclass
class _Page:
    scope_key: str
    page: object
    revision: int = 0


class BrowserWorkerV2:
    def __init__(self, config: BrowserConfig):
        self.config = config
        self._playwright = None
        self._browser = None
        self._pages: dict[str, _Page] = {}
        self._lock = asyncio.Lock()

    def _host_allowed(self, host: str | None) -> bool:
        if not host or host in {'localhost', 'metadata.google.internal'}:
            return False
        try:
            address = ipaddress.ip_address(host)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return False
        except ValueError:
            pass
        return any(host == allowed or allowed.startswith('*.') and host.endswith(allowed[1:])
                   for allowed in self.config.allowed_hosts)

    @staticmethod
    def _address_allowed(address: str) -> bool:
        """Check a DNS answer for SSRF classes without reapplying host ACLs."""
        try:
            value = ipaddress.ip_address(address)
        except ValueError:
            # getaddrinfo should normally return numeric addresses; leave an
            # unusual textual answer to the browser's own connection failure.
            return True
        return not (value.is_private or value.is_loopback or value.is_link_local
                    or value.is_reserved or value.is_unspecified)

    def validate_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not self._host_allowed(parsed.hostname):
            raise ValueError('browser URL must be an allowed public HTTP(S) host')

    async def validate_network(self, url: str):
        self.validate_url(url)
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.run_in_executor(None, socket.getaddrinfo, parsed.hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError('browser host DNS resolution failed') from error
        if not infos or any(not self._address_allowed(item[4][0]) for item in infos):
            raise ValueError('browser host resolves to a private or otherwise blocked address')

    async def _route(self, route):
        try:
            await self.validate_network(route.request.url)
        except ValueError:
            await route.abort()
            return
        await route.continue_()

    async def _new_page(self, scope_key: str, url: str) -> _Page:
        await self.validate_network(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError('Playwright is not installed for the browser worker') from None
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.config.headless)
            context = await self._browser.new_context(service_workers='block')
            await context.route('**/*', self._route)
            page = await context.new_page()
            handle = _Page(scope_key=scope_key, page=page)
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=int(self.config.timeout_seconds * 1000))
            except BaseException:
                await context.close()
                raise
            return handle

    def _get(self, scope_key: str, page_ref: str) -> _Page:
        handle = self._pages.get(page_ref)
        if handle is None or handle.scope_key != scope_key:
            raise ValueError('页面不存在、已关闭或不属于当前工作')
        return handle

    async def _snapshot(self, handle: _Page, page_ref: str) -> dict:
        page = handle.page
        await page.locator('body').evaluate("""body => {
            const nodes = body.querySelectorAll('a,button,input,textarea,select,[role]');
            nodes.forEach((node, index) => node.setAttribute('data-lenbot-ref', `e${index + 1}`));
        }""")
        elements = await page.locator('[data-lenbot-ref]').evaluate_all("""nodes => nodes.slice(0, 120).map(node => ({
            ref: node.getAttribute('data-lenbot-ref'), tag: node.tagName.toLowerCase(),
            role: node.getAttribute('role'), text: (node.innerText || node.getAttribute('aria-label') || '').slice(0, 200)
        }))""")
        text = (await page.locator('body').inner_text())[:self.config.max_text_chars]
        handle.revision += 1
        return {'page_ref': page_ref, 'snapshot_revision': handle.revision, 'url': page.url,
            'title': await page.title(), 'text': text, 'elements': elements,
            'coverage': 'browser_dom_text', 'pixels_loaded': False}

    async def open(self, scope_key: str, request: BrowserOpenInput) -> dict:
        handle = await self._new_page(scope_key, request.url)
        ref = 'page_' + uuid.uuid4().hex[:20]
        self._pages[ref] = handle
        return await self._snapshot(handle, ref)

    async def snapshot(self, scope_key: str, request: BrowserPageInput) -> dict:
        handle = self._get(scope_key, request.page_ref)
        return await self._snapshot(handle, request.page_ref)

    async def interact(self, scope_key: str, request: BrowserInteractInput) -> dict:
        if not self.config.allow_interactions:
            raise PermissionError('browser interactions are disabled by configuration')
        handle = self._get(scope_key, request.page_ref)
        if request.snapshot_revision != handle.revision:
            raise ValueError('页面观察版本已过期，请重新 browser_snapshot')
        page = handle.page
        if request.action == 'scroll':
            try:
                amount = int(request.value or '700')
            except ValueError:
                raise ValueError('scroll value must be an integer') from None
            await page.mouse.wheel(0, max(-3000, min(3000, amount)))
        else:
            if not request.element_ref:
                raise ValueError('click requires an element_ref from the current snapshot')
            await page.locator(f'[data-lenbot-ref="{request.element_ref}"]').click(
                timeout=int(self.config.timeout_seconds * 1000))
        return await self._snapshot(handle, request.page_ref)

    async def capture(self, scope_key: str, request: BrowserCaptureInput) -> bytes:
        handle = self._get(scope_key, request.page_ref)
        if request.snapshot_revision is not None and request.snapshot_revision != handle.revision:
            raise ValueError('页面观察版本已过期，请重新 browser_snapshot')
        return await handle.page.screenshot(type='png', full_page=request.area == 'full_page')

    async def close_scope(self, scope_key: str):
        refs = [ref for ref, handle in self._pages.items() if handle.scope_key == scope_key]
        for ref in refs:
            handle = self._pages.pop(ref)
            try:
                await handle.page.context.close()
            except Exception:
                pass

    async def close(self):
        for handle in list(self._pages.values()):
            try:
                await handle.page.context.close()
            except Exception:
                pass
        self._pages.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
