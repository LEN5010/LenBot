"""Fetch what a card needs from public Bilibili endpoints, then inline it.

The card renders offline, so every picture must become a data: URI before it
reaches the template. Anything that fails here returns empty and the card
renders without that picture -- a missing avatar is a better outcome than a
push that never goes out.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from len_bot.media.service import validate_image

from .models import AuthorProfile

logger = logging.getLogger(__name__)

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
REFERER = 'https://www.bilibili.com/'
CARD_API_URL = 'https://api.bilibili.com/x/web-interface/card'

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_CONCURRENCY = 6
REQUEST_TIMEOUT_SECONDS = 15.0


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, trust_env=False, follow_redirects=True,
        headers={'User-Agent': USER_AGENT, 'Referer': REFERER})


async def fetch_profile(uid: str, *, client: httpx.AsyncClient | None = None) -> AuthorProfile:
    """Name, avatar, pendant and the three footer counters in one request.

    `x/web-interface/card` needs no wbi signature and no login, which is why
    it is preferred over the signed space endpoints for a push card: the whole
    footer arrives from one public GET.
    """
    owned = client is None
    http = client or _client(REQUEST_TIMEOUT_SECONDS)
    try:
        response = await http.get(CARD_API_URL, params={'mid': str(uid), 'photo': 'true'})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning('Bilibili card profile for %s failed: %s', uid, error)
        return AuthorProfile(uid=str(uid))
    finally:
        if owned:
            await http.aclose()
    if not isinstance(payload, dict) or payload.get('code') != 0:
        logger.warning('Bilibili card profile for %s returned code %r', uid,
                       (payload or {}).get('code') if isinstance(payload, dict) else None)
        return AuthorProfile(uid=str(uid))
    data = payload.get('data') or {}
    card = data.get('card') or {}

    def count(value):
        return value if isinstance(value, int) and value >= 0 else None

    return AuthorProfile(
        uid=str(card.get('mid') or uid),
        name=str(card.get('name') or ''),
        avatar_url=str(card.get('face') or ''),
        pendant_url=str(((card.get('pendant') or {}).get('image')) or ''),
        total_likes=count(data.get('like_num')),
        following=count(card.get('attention')),
        follower=count(card.get('fans')),
    )


class ProfileCache:
    """UP profiles move slowly, and a push should never wait on one twice.

    A refresh that fails keeps the last good profile rather than replacing it
    with blanks: a stale follower count reads better than `--`.
    """

    def __init__(self, ttl_seconds: float = 6 * 3600):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, AuthorProfile]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _fresh(self, uid: str, now: float):
        entry = self._entries.get(uid)
        return entry[1] if entry and now - entry[0] < self.ttl_seconds else None

    async def get(self, uid, *, now: float) -> AuthorProfile:
        uid = str(uid)
        cached = self._fresh(uid, now)
        if cached is not None:
            return cached
        async with self._locks.setdefault(uid, asyncio.Lock()):
            cached = self._fresh(uid, now)
            if cached is not None:
                return cached
            profile = await fetch_profile(uid)
            previous = self._entries.get(uid)
            if not profile.name and previous is not None:
                return previous[1]
            self._entries[uid] = (now, profile.model_copy(update={'fetched_at': now}))
            return self._entries[uid][1]


async def _fetch_one(http: httpx.AsyncClient, url: str, gate: asyncio.Semaphore) -> tuple[str, str]:
    async with gate:
        try:
            response = await http.get(url)
            response.raise_for_status()
            data = response.content
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ValueError(f'image is empty or over {MAX_IMAGE_BYTES} bytes')
            # Trust the bytes, not the URL suffix or the served content-type.
            mime = await asyncio.to_thread(
                validate_image, data, max_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_IMAGE_PIXELS)
        except (httpx.HTTPError, ValueError, OSError) as error:
            logger.info('Card image %s skipped: %s', url, error)
            return url, ''
    return url, f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')


async def inline_images(urls, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    """Resolve each URL to a data: URI; failures map to '' and are dropped."""
    wanted = [url for url in dict.fromkeys(urls) if url]
    if not wanted:
        return {}
    gate = asyncio.Semaphore(IMAGE_CONCURRENCY)
    async with _client(timeout) as http:
        pairs = await asyncio.gather(*(_fetch_one(http, url, gate) for url in wanted))
    return {url: encoded for url, encoded in pairs if encoded}
