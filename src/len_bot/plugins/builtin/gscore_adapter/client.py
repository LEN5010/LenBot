"""One owned WebSocket connection; it never talks to OneBot directly."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from .config import GscoreConfig

logger = logging.getLogger(__name__)


class GscoreClient:
    def __init__(self, config: GscoreConfig):
        self.config = config
        self._socket = None
        self._lock = asyncio.Lock()

    async def connect(self):
        if self._socket is not None:
            return
        headers = {'Authorization': f'Bearer {self.config.access_token}'} if self.config.access_token else None
        self._socket = await websockets.connect(self._url(), additional_headers=headers,
            open_timeout=self.config.connect_timeout_seconds)

    async def close(self):
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    def _url(self) -> str:
        if not self.config.access_token or not self.config.token_query_parameter:
            return self.config.ws_url
        parts = urlsplit(self.config.ws_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault(self.config.token_query_parameter, self.config.access_token)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    async def send_frame(self, payload: dict) -> None:
        async with self._lock:
            await self.connect()
            await self._socket.send(json.dumps(payload, ensure_ascii=False))

    async def receive_once(self) -> dict:
        await self.connect()
        raw = await asyncio.wait_for(self._socket.recv(), timeout=self.config.command_timeout_seconds)
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('GSUID Core message must be a JSON object')
        return value
