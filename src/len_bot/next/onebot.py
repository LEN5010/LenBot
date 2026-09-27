"""OneBot Universal WebSocket and explicitly selected HTTP action transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from http import HTTPStatus
from urllib.parse import quote

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .config import OneBotForward, OneBotReverse
from .messages import ChatMessage, SendResult, UploadResult, parse_send_result, parse_upload_result


def _non_json_number(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class OneBotCallError(RuntimeError):
    """Whether an unsuccessful call had entered the actual send phase."""

    def __init__(self, message: str, *, submitted: bool):
        super().__init__(message)
        self.submitted = submitted


class OneBot:
    def __init__(self, settings: OneBotForward | OneBotReverse, *, bot_qq: str,
                 on_event: Callable[[dict], None], on_error: Callable[[str], None],
                 on_connection_change: Callable[[], None] | None = None):
        self.settings, self.bot_qq = settings, bot_qq
        self.on_event, self.on_error = on_event, on_error
        self.on_connection_change = on_connection_change
        self._running = False
        self._ws: ClientConnection | ServerConnection | None = None
        self._server: Server | None = None
        self._receiver: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None
        self._connection_changed = asyncio.Event()
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._sequence = 0
        self._verified_ws: ClientConnection | ServerConnection | None = None
        self._identity_lock = asyncio.Lock()

    def _safe(self, text: str) -> str:
        if self.settings.access_token:
            text = text.replace(json.dumps(self.settings.access_token, ensure_ascii=False)[1:-1], "[redacted]")
            text = text.replace(repr(self.settings.access_token)[1:-1], "[redacted]")
            text = text.replace(self.settings.access_token, "[redacted]")
        return text[:2000]

    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def addresses(self) -> list[tuple]:
        return [] if self._server is None else [socket.getsockname() for socket in self._server.sockets]

    async def __aenter__(self) -> OneBot:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("OneBot transport is already started; close it before restarting")
        self._running = True
        self._connection_changed.clear()
        headers = ({"Authorization": f"Bearer {self.settings.access_token}"}
                   if self.settings.access_token else {})
        options = dict(
            open_timeout=self.settings.request_timeout_seconds,
            close_timeout=self.settings.request_timeout_seconds,
            ping_interval=self.settings.ping_interval_seconds,
            ping_timeout=self.settings.ping_timeout_seconds,
            max_size=self.settings.max_frame_bytes,
        )
        try:
            if self.settings.action_transport == "http":
                self._http = httpx.AsyncClient(
                    headers=headers, timeout=self.settings.request_timeout_seconds,
                    trust_env=False, follow_redirects=False,
                )
            if isinstance(self.settings, OneBotForward):
                websocket = await connect(self.settings.ws_url, additional_headers=headers, proxy=None, **options)
                self._attach(websocket)
                self._receiver = asyncio.create_task(self._consume(websocket))
            else:
                self._server = await serve(
                    self._accept, self.settings.listen_host, self.settings.listen_port,
                    process_request=self._handshake, **options,
                )
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=False) from None

    async def close(self) -> None:
        self._running = False
        self._verified_ws = None
        self._connection_changed.set()
        if self._server is not None:
            self._server.close()
        try:
            if self._ws is not None:
                await self._ws.close(code=1001, reason="OneBot transport stopped")
            if self._receiver is not None:
                await self._receiver
        finally:
            self._receiver = None
            try:
                if self._server is not None:
                    await self._server.wait_closed()
            finally:
                self._server = None
                if self._http is not None:
                    await self._http.aclose()
                    self._http = None

    async def wait_terminated(self) -> None:
        """Wait for the started transport, not a reverse peer's single session."""
        if isinstance(self.settings, OneBotForward):
            await asyncio.shield(self._receiver)
        else:
            await self._server.wait_closed()

    async def wait_connected(self, timeout_seconds: float | None) -> None:
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    if not self._running:
                        raise OneBotCallError("OneBot transport is not running", submitted=False)
                    if self.connected:
                        return
                    self._connection_changed.clear()
                    await self._connection_changed.wait()
        except TimeoutError as error:
            raise OneBotCallError(f"{type(error).__name__}: OneBot connection did not arrive within {timeout_seconds}s",
                                  submitted=False) from None

    def _handshake(self, connection: ServerConnection, request: Request) -> Response | None:
        if not self._running:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "OneBot transport is stopping\n")
        if self.settings.access_token and request.headers.get("Authorization") != f"Bearer {self.settings.access_token}":
            return connection.respond(HTTPStatus.FORBIDDEN, "OneBot authorization failed\n")
        if request.headers.get("X-Client-Role") != "Universal":
            return connection.respond(HTTPStatus.BAD_REQUEST, "OneBot Universal role is required\n")
        if request.headers.get("X-Self-ID") != self.bot_qq:
            return connection.respond(HTTPStatus.FORBIDDEN, "OneBot account does not match\n")
        if self._ws is not None:
            return connection.respond(HTTPStatus.CONFLICT, "OneBot already has an active connection\n")
        return None

    def _attach(self, websocket: ClientConnection | ServerConnection) -> None:
        self._verified_ws = None
        self._ws = websocket
        self._connection_changed.set()
        if self.on_connection_change is not None:
            self.on_connection_change()

    async def _accept(self, websocket: ServerConnection) -> None:
        # Two handshakes may finish before either handler attaches its socket.
        if self._ws is not None or not self._running:
            await websocket.close(code=1008, reason="OneBot connection is not available")
            return
        self._attach(websocket)
        await self._consume(websocket)

    @staticmethod
    def _object(raw: str | bytes) -> dict:
        value = json.loads(raw, parse_constant=_non_json_number)
        if not isinstance(value, dict):
            raise ValueError("OneBot frame must be a JSON object")
        return value

    async def _consume(self, websocket: ClientConnection | ServerConnection) -> None:
        try:
            while True:
                try:
                    frame = await websocket.recv()
                except ConnectionClosed as error:
                    reason = self._safe(f"{type(error).__name__}: {error}")
                    if self._running:
                        self.on_error(reason)
                    return
                try:
                    packet = self._object(frame)
                    if "echo" in packet:
                        echo = packet["echo"]
                        if not isinstance(echo, str):
                            raise ValueError("OneBot reply echo does not match the sent string echo")
                        future = self._pending.get(echo)
                        if future is None or future.done():
                            raise ValueError(f"OneBot reply has no active request: {echo}")
                        future.set_result(packet)
                    elif packet.get("post_type") in {"message", "notice", "request", "meta_event"}:
                        if str(packet["self_id"]) != self.bot_qq:
                            raise ValueError("OneBot event account does not match")
                        self.on_event(packet)
                    else:
                        raise ValueError("OneBot frame is neither an event nor an echoed response")
                except Exception as error:
                    raw = frame if isinstance(frame, str) else frame.decode("utf-8", errors="backslashreplace")
                    self.on_error(self._safe(f"{type(error).__name__}: {error}; raw={raw}"))
        except BaseException as error:
            reason = self._safe(f"{type(error).__name__}: {error}")
            raise
        finally:
            if self._ws is websocket:
                self._ws = None
                self._verified_ws = None
                self._connection_changed.set()
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(OneBotCallError(reason, submitted=True))
                if self.on_connection_change is not None:
                    self.on_connection_change()
            await websocket.close()

    async def call(self, action: str, params: dict) -> dict:
        if not self._running:
            raise OneBotCallError("OneBot transport has not been started", submitted=False)
        if self.settings.action_transport == "http":
            return await self._call_http(action, params)
        websocket = self._ws
        if websocket is None:
            raise OneBotCallError("OneBot WebSocket is not connected; request was not sent", submitted=False)
        return await self._call_ws_on(websocket, action, params)

    async def _call_ws_on(self, websocket: ClientConnection | ServerConnection,
                          action: str, params: dict) -> dict:
        if not self._running or self._ws is not websocket:
            raise OneBotCallError("Verified OneBot WebSocket is no longer connected; request was not sent",
                                  submitted=False)
        self._sequence += 1
        echo = str(self._sequence)
        try:
            payload = json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=False) from None
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await websocket.send(payload)
                return await future
        except OneBotCallError:
            raise
        except Exception as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=True) from None
        finally:
            self._pending.pop(echo)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # A disconnect may resolve it while socket.send is still awaiting.

    def _check_login_info(self, raw: dict, endpoint: str) -> None:
        data = raw.get("data")
        retcode = raw.get("retcode")
        user_id = data.get("user_id") if isinstance(data, dict) else None
        if (raw.get("status") != "ok" or type(retcode) is not int or retcode != 0
                or isinstance(user_id, bool) or not isinstance(user_id, (int, str))
                or not str(user_id) or str(user_id) != self.bot_qq):
            raise ValueError(f"{endpoint} get_login_info did not confirm configured bot_qq "
                             f"{self.bot_qq}; raw={raw!r}")

    async def _verify_identity(self, websocket: ClientConnection | ServerConnection) -> None:
        async with self._identity_lock:
            if not self._running or self._ws is not websocket:
                raise OneBotCallError("OneBot WebSocket changed during identity check; message was not sent",
                                      submitted=False)
            if self._verified_ws is websocket:
                return
            ws_info = await self._call_ws_on(websocket, "get_login_info", {})
            self._check_login_info(ws_info, "WebSocket")
            if not self._running or self._ws is not websocket:
                raise OneBotCallError("OneBot WebSocket changed during identity check; message was not sent",
                                      submitted=False)
            if self.settings.action_transport == "http":
                http_info = await self._call_http("get_login_info", {})
                self._check_login_info(http_info, "HTTP")
                if not self._running or self._ws is not websocket:
                    raise OneBotCallError("OneBot WebSocket changed during identity check; message was not sent",
                                          submitted=False)
            self._verified_ws = websocket

    async def send_text(self, message: ChatMessage) -> SendResult:
        kind, separator, target = message.scene.partition(":")
        if (not separator or kind not in {"group", "private"} or not target.isdecimal()
                or int(target) <= 0 or str(int(target)) != target):
            return SendResult("failed", None, f"Invalid OneBot scene: {message.scene!r}")
        unsupported = [segment.type for segment in message.segments
                       if segment.type not in {"text", "at", "reply"}]
        if unsupported:
            return SendResult("failed", None, f"Unsupported OneBot text segments: {unsupported!r}")
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        params = {"group_id" if kind == "group" else "user_id": int(target),
                  "message": [{"type": segment.type, "data": segment.data}
                              for segment in message.segments]}
        websocket = self._ws
        if not self._running or websocket is None:
            return SendResult("failed", None, "OneBot WebSocket is not connected; message was not sent")
        try:
            await self._verify_identity(websocket)
        except (OneBotCallError, ValueError) as error:
            return SendResult("failed", None, self._safe(f"Identity verification failed: {error}"))
        if not self._running or self._ws is not websocket or self._verified_ws is not websocket:
            return SendResult("failed", None, "OneBot WebSocket changed before text send; message was not sent")
        try:
            raw = (await self._call_http(action, params) if self.settings.action_transport == "http"
                   else await self._call_ws_on(websocket, action, params))
        except OneBotCallError as error:
            return SendResult("unconfirmed" if error.submitted else "failed", None, self._safe(str(error)))
        result = parse_send_result(raw)
        if result.status == "sent":
            return result
        return SendResult(result.status, None, self._safe(repr(raw)))

    async def upload_file(self, scene: str, file: str, name: str) -> UploadResult:
        """Upload an already registered, OneBot-visible file; return only the API receipt."""
        kind, separator, target = scene.partition(":")
        if (not separator or kind not in {"group", "private"} or not target.isdecimal()
                or int(target) <= 0 or str(int(target)) != target):
            return UploadResult("failed", None, f"Invalid OneBot scene: {scene!r}", None)
        if not file or not name:
            return UploadResult("failed", None, "OneBot upload requires a file path and name", None)
        action = "upload_group_file" if kind == "group" else "upload_private_file"
        params = {"group_id" if kind == "group" else "user_id": int(target),
                  "file": file, "name": name}
        websocket = self._ws
        if not self._running or websocket is None:
            return UploadResult("failed", None, "OneBot WebSocket is not connected; file was not uploaded", None)
        try:
            await self._verify_identity(websocket)
        except (OneBotCallError, ValueError) as error:
            return UploadResult("failed", None, self._safe(f"Identity verification failed: {error}"), None)
        if not self._running or self._ws is not websocket or self._verified_ws is not websocket:
            return UploadResult("failed", None, "OneBot WebSocket changed before file upload; file was not uploaded", None)
        try:
            raw = (await self._call_http(action, params) if self.settings.action_transport == "http"
                   else await self._call_ws_on(websocket, action, params))
        except OneBotCallError as error:
            return UploadResult("unconfirmed" if error.submitted else "failed", None,
                                self._safe(str(error)), None)
        return parse_upload_result(raw)

    async def _call_http(self, action: str, params: dict) -> dict:
        url = self.settings.http_url.rstrip("/") + "/" + quote(action, safe="")
        try:
            request = self._http.build_request("POST", url, json=params)
        except Exception as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=False) from None
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                response = await self._http.send(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=False) from None
        except Exception as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}"), submitted=True) from None
        if not response.is_success:
            raise OneBotCallError(self._safe(f"OneBot HTTP {response.status_code}: {response.text}"), submitted=True)
        try:
            return self._object(response.text)
        except (ValueError, TypeError) as error:
            raise OneBotCallError(self._safe(f"{type(error).__name__}: {error}; raw={response.text}"), submitted=True) from None
