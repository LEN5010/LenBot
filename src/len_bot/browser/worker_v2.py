"""Task-scoped Playwright observation worker with revisioned element refs."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .models import BrowserCaptureInput, BrowserInteractInput, BrowserOpenInput, BrowserPageInput
from .worker import BrowserConfig

MAX_FULL_PAGE_HEIGHT = 8192
MAX_FULL_PAGE_BYTES = 4 * 1024 * 1024


@dataclass
class _Page:
    scope_key: str
    page: object
    revision: int = 0
    text_offset: int = 0
    text_limit: int | None = None
    body_text: str | None = None
    title: str = ''
    url: str = ''
    elements: list | None = None
    collection_truncated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserWorkerV2:
    def __init__(self, config: BrowserConfig, *, proxy: dict | None = None):
        self.config = config
        self._playwright = None
        self._browser = None
        self._pages: dict[str, _Page] = {}
        self._lock = asyncio.Lock()
        self._proxy = proxy
        self._contexts = {}
        self._opening = {}
        self._closing_scopes = set()

    def _host_allowed(self, host: str | None) -> bool:
        if not host or host in {'localhost', 'metadata.google.internal'}:
            return False
        try:
            address = ipaddress.ip_address(host)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return False
        except ValueError:
            pass
        return '*' in self.config.allowed_hosts or any(
            host == allowed or allowed.startswith('*.') and host.endswith(allowed[1:])
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
        if parsed.username or parsed.password or parsed.scheme not in {'http', 'https'} or not self._host_allowed(parsed.hostname):
            raise ValueError('browser URL must be an allowed public HTTP(S) host')

    async def validate_network(self, url: str):
        self.validate_url(url)
        if self._proxy is not None:
            # Gateway's proxy owns DNS and address admission. The container
            # has no direct DNS/network route outside that proxy.
            return
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
            if scope_key in self._closing_scopes:
                raise ValueError('本工作浏览器会话已经结束')
            if self._browser is None:
                self._playwright = await async_playwright().start()
                # Keep Chromium's OS sandbox enabled for the host-side browser
                # worker; Python workspace isolation does not protect this
                # separate process tree.
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.config.headless, chromium_sandbox=True, proxy=self._proxy,
                        args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
                except BaseException:
                    await asyncio.shield(self._playwright.stop())
                    self._playwright = None
                    raise
            creating = asyncio.create_task(self._browser.new_context(service_workers='block', accept_downloads=False))
            try:
                context = await asyncio.shield(creating)
            except asyncio.CancelledError:
                context = await creating
                await asyncio.shield(context.close())
                raise
            self._contexts[context] = scope_key
        try:
            await context.route('**/*', self._route)
            page = await context.new_page()
            # Popups cannot create an uncounted page outside the tool contract.
            context.on('page', lambda extra: asyncio.create_task(extra.close()) if extra is not page else None)
            await page.goto(url, wait_until='domcontentloaded', timeout=int(self.config.timeout_seconds * 1000))
            if scope_key in self._closing_scopes:
                raise ValueError('本工作在页面打开期间已经结束')
            return _Page(scope_key=scope_key, page=page)
        except BaseException:
            self._contexts.pop(context, None)
            await asyncio.shield(context.close())
            raise

    def _get(self, scope_key: str, page_ref: str) -> _Page:
        handle = self._pages.get(page_ref)
        if handle is None or handle.scope_key != scope_key:
            raise ValueError('页面不存在、已关闭或不属于当前工作')
        return handle

    def _page_view(self, handle: _Page, page_ref: str) -> dict:
        full_text = handle.body_text or ''
        offset = handle.text_offset
        limit = min(handle.text_limit or self.config.max_text_chars, self.config.max_text_chars)
        text = full_text[offset:offset + limit]
        next_offset = offset + len(text) if offset + len(text) < len(full_text) else None
        return {'page_ref': page_ref, 'snapshot_revision': handle.revision, 'url': handle.url,
            'title': handle.title, 'text': text, 'elements': list(handle.elements or []),
            'text_offset': offset, 'text_limit': limit, 'text_total_chars': len(full_text),
            'text_next_offset': next_offset, 'text_truncated': next_offset is not None,
            'collection_truncated': handle.collection_truncated,
            'collection_limit_chars': self.config.max_snapshot_chars,
            'coverage': 'browser_dom_text', 'pixels_loaded': False}

    async def _snapshot(self, handle: _Page, page_ref: str) -> dict:
        page = handle.page
        collected = await page.locator('body').evaluate("""(body, limit) => {
            const elements = [], chunks = [];
            const walker = document.createTreeWalker(body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
            let node, size = 0, visited = 0, truncated = false;
            while ((node = walker.nextNode())) {
                if (++visited > 100000) { truncated = true; break; }
                if (node.nodeType === Node.ELEMENT_NODE) {
                    node.removeAttribute('data-lenbot-ref');
                    if (elements.length < 120 && node.matches('a,button,input,textarea,select,[role]')) {
                        const ref = 'e' + (elements.length + 1);
                        node.setAttribute('data-lenbot-ref', ref);
                        elements.push({ref, tag: node.tagName.toLowerCase(), role: node.getAttribute('role'),
                            text: (node.getAttribute('aria-label') || node.textContent || '').slice(0,200)});
                    }
                    continue;
                }
                const parent = node.parentElement;
                if (!parent || parent.closest('script,style,noscript,template') || !parent.getClientRects().length) continue;
                const style = getComputedStyle(parent);
                if (style.visibility === 'hidden' || style.display === 'none') continue;
                const value = node.nodeValue || '';
                const available = limit - size;
                if (value.length > available) { chunks.push(value.slice(0, available)); truncated = true; break; }
                chunks.push(value); size += value.length;
                if (size < limit) { chunks.push('\\n'); size++; }
                else { truncated = true; break; }
            }
            return {text: chunks.join(''), elements, truncated};
        }""", self.config.max_snapshot_chars)
        handle.body_text = collected['text']
        handle.collection_truncated = collected['truncated']
        handle.title = await page.evaluate('() => document.title.slice(0,2000)')
        handle.url = page.url
        handle.elements = collected['elements']
        handle.revision += 1
        return self._page_view(handle, page_ref)

    async def open(self, scope_key: str, request: BrowserOpenInput) -> dict:
        async with self._lock:
            opened = sum(1 for handle in self._pages.values() if handle.scope_key == scope_key)
            if scope_key in self._closing_scopes or opened + self._opening.get(scope_key, 0) >= self.config.max_open_pages:
                raise ValueError(f'会话已结束或页面额度已达 {self.config.max_open_pages}')
            self._opening[scope_key] = self._opening.get(scope_key, 0) + 1
        handle, ref = None, 'page_' + uuid.uuid4().hex[:20]
        try:
            handle = await self._new_page(scope_key, request.url)
            value = await self._snapshot(handle, ref)
            if scope_key in self._closing_scopes:
                raise ValueError('本工作在页面采集期间已经结束')
            self._pages[ref] = handle
            return value
        except BaseException:
            if handle:
                self._contexts.pop(handle.page.context, None)
                await asyncio.shield(handle.page.context.close())
            raise
        finally:
            async with self._lock:
                self._opening[scope_key] -= 1

    async def snapshot(self, scope_key: str, request: BrowserPageInput) -> dict:
        handle = self._get(scope_key, request.page_ref)
        async with handle.lock:
            return await self._snapshot_request(handle, request)

    async def _snapshot_request(self, handle, request):
        handle.text_offset = request.text_offset
        handle.text_limit = request.text_limit
        if request.refresh or handle.body_text is None:
            return await self._snapshot(handle, request.page_ref)
        return self._page_view(handle, request.page_ref)

    async def interact(self, scope_key: str, request: BrowserInteractInput) -> dict:
        if not self.config.allow_interactions:
            raise PermissionError('browser interactions are disabled by configuration')
        handle = self._get(scope_key, request.page_ref)
        async with handle.lock:
            return await self._interact(handle, request)

    async def _interact(self, handle, request):
        handle.text_offset = request.text_offset
        handle.text_limit = request.text_limit
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
        async with handle.lock:
            return await self._capture(handle, request)

    async def _capture(self, handle, request):
        if request.snapshot_revision is not None and request.snapshot_revision != handle.revision:
            raise ValueError('页面观察版本已过期，请重新 browser_snapshot')
        if request.area == 'full_page':
            height = await handle.page.evaluate(
                '() => Math.ceil((document.documentElement && document.documentElement.scrollHeight) || 0)')
            if not isinstance(height, (int, float)) or height > MAX_FULL_PAGE_HEIGHT:
                raise ValueError(
                    f'全页高度超过 {MAX_FULL_PAGE_HEIGHT}px 上限，请改用 viewport 或分段截图，不要声称已看完全页')
        png = await handle.page.screenshot(type='png', full_page=request.area == 'full_page')
        if len(png) > MAX_FULL_PAGE_BYTES:
            raise ValueError(
                f'截图超过 {MAX_FULL_PAGE_BYTES} 字节上限，请改用 viewport 或分段截图，不要声称已看完全页')
        return png

    async def close_scope(self, scope_key: str):
        self._closing_scopes.add(scope_key)
        refs = [ref for ref, handle in self._pages.items() if handle.scope_key == scope_key]
        for ref in refs:
            handle = self._pages.pop(ref)
        contexts = [context for context, scope in self._contexts.items() if scope == scope_key]
        errors = []
        for context in contexts:
            self._contexts.pop(context, None)
            try:
                await context.close()
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError('浏览器 Context 清理未确认：' + '; '.join(errors))

    async def close(self):
        self._closing_scopes.update(self._opening)
        self._closing_scopes.update(self._contexts.values())
        self._pages.clear()
        try:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
                self._contexts.clear()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
