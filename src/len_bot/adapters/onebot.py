import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Optional, Callable, Awaitable
import httpx
import websockets
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.actions.models import ActionItem, ActionType, DeliveryResult, DeliveryStatus

logger = logging.getLogger(__name__)

class OneBotAdapter:
    def __init__(
        self,
        config: RuntimeConfig,
        on_event: Callable[[Event], Awaitable[None]],
        on_self_id: Optional[Callable[[int], None]] = None,
    ):
        self.config = config
        self.on_event = on_event
        self.on_self_id = on_self_id
        self._server = None
        self._active_ws = None
        self._client_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._last_error: Optional[str] = None
        self._echo_counter = 0
        self._pending_requests: dict[str, asyncio.Future[dict]] = {}
        self._own_message_ids: deque[str] = deque(maxlen=1000)
        self._self_id: Optional[int] = None

    async def start(self) -> None:
        self._stopping = False
        self._last_error = None
        if self.config.onebot_connection_mode == "forward_ws":
            self._client_task = asyncio.create_task(self._forward_connection_loop())
            logger.info("OneBot forward WebSocket connector started")
            return
        self._server = await websockets.serve(
            self._handle_connection,
            self.config.ws_host,
            self.config.ws_port,
            ping_interval=self.config.onebot_ping_interval_seconds,
            ping_timeout=self.config.onebot_ping_timeout_seconds,
        )
        logger.info("OneBot reverse WebSocket server started on ws://%s:%s", self.config.ws_host, self.config.ws_port)

    def restore_own_message_ids(self, message_ids: list[str]) -> None:
        self._own_message_ids.clear()
        self._own_message_ids.extend(message_ids)

    async def stop(self) -> None:
        self._stopping = True
        if self._client_task:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None
        if self._active_ws:
            await self._active_ws.close(code=1001, reason="LenBot connection restart")
            self._active_ws = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._fail_pending(ConnectionError("OneBot connection closed"))

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    @property
    def connected(self) -> bool:
        return self._active_ws is not None

    def status(self) -> dict:
        return {
            "connection_mode": self.config.onebot_connection_mode,
            "action_transport": self.config.onebot_action_transport,
            "ws_url": self.config.onebot_ws_url,
            "http_url": self.config.onebot_http_url,
            "host": self.config.ws_host,
            "port": self.config.ws_port,
            "connected": self.connected,
            "remote_address": self._remote_address(),
            "server_status": "listening" if self._server else "stopped",
            "connector_status": "running" if self._client_task and not self._client_task.done() else "stopped",
            "last_error": self._last_error,
            "access_token_set": bool(self.config.onebot_access_token),
            "echo_counter": self._echo_counter,
            "self_id": self._self_id,
        }

    def _remote_address(self) -> Optional[str]:
        if not self._active_ws:
            return None
        try:
            return str(self._active_ws.remote_address)
        except Exception:
            return "connected"

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.onebot_access_token.strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _is_authorized(self, websocket) -> bool:
        expected = self.config.onebot_access_token.strip()
        if not expected:
            return True
        request = getattr(websocket, "request", None)
        actual = request.headers.get("Authorization", "") if request else ""
        return actual == f"Bearer {expected}"

    async def _forward_connection_loop(self) -> None:
        retry_delay = self.config.onebot_reconnect_seconds
        while not self._stopping:
            try:
                async with websockets.connect(
                    self.config.onebot_ws_url,
                    additional_headers=self._auth_headers(),
                    proxy=None,
                    open_timeout=self.config.onebot_request_timeout_seconds,
                    ping_interval=self.config.onebot_ping_interval_seconds,
                    ping_timeout=self.config.onebot_ping_timeout_seconds,
                ) as websocket:
                    self._last_error = None
                    retry_delay = self.config.onebot_reconnect_seconds
                    await self._consume_connection(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._last_error = type(error).__name__
                logger.warning("OneBot forward WebSocket connection failed: %s", self._last_error)
            if not self._stopping:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, self.config.onebot_reconnect_max_seconds)

    async def send_action(self, action: ActionItem) -> DeliveryResult:
        """Sends action to connected OneBot client via JSON-RPC."""
        if self.config.onebot_action_transport == "http":
            return await self._send_http_action(action)
        if not self._active_ws:
            logger.warning("Cannot send action: No OneBot client connected")
            return DeliveryResult(status=DeliveryStatus.NOT_SENT, transport="websocket", error="OneBot 未连接，请求未发出")

        self._echo_counter += 1
        echo = f"echo_{self._echo_counter}"
        endpoint, params = self._action_payload(action)

        payload = {
            "action": endpoint,
            "params": params,
            "echo": echo
        }

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[echo] = fut

        try:
            await self._active_ws.send(json.dumps(payload))
            res = await asyncio.wait_for(fut, timeout=self.config.onebot_request_timeout_seconds)
            return self._delivery_response(res, "websocket")
        except Exception as e:
            return DeliveryResult(status=DeliveryStatus.UNKNOWN, transport="websocket",
                                  error_code=type(e).__name__, error="请求已进入发送阶段，但未取得可靠确认")
        finally:
            self._pending_requests.pop(echo, None)

    async def _send_http_action(self, action: ActionItem) -> DeliveryResult:
        endpoint, params = self._action_payload(action)
        url = f"{self.config.onebot_http_url.rstrip('/')}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=self.config.onebot_request_timeout_seconds, headers=self._auth_headers(), trust_env=False) as client:
                response = await client.post(url, json=params)
                if not response.is_success:
                    return DeliveryResult(status=DeliveryStatus.UNKNOWN, transport="http",
                                          error_code=str(response.status_code), error="HTTP 返回异常状态，无法确认消息是否发送")
                data = response.json()
            return self._delivery_response(data, "http")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            return DeliveryResult(status=DeliveryStatus.NOT_SENT, transport="http",
                                  error_code=type(error).__name__, error="HTTP 连接未建立，请求未发出")
        except Exception as error:
            return DeliveryResult(status=DeliveryStatus.UNKNOWN, transport="http",
                                  error_code=type(error).__name__, error="HTTP 请求未取得可靠发送确认")

    def _delivery_response(self, data: dict, transport: str) -> DeliveryResult:
        if self._response_ok(data):
            self._remember_own_message(data)
            message_id = (data.get("data") or {}).get("message_id")
            return DeliveryResult(status=DeliveryStatus.SENT, transport=transport,
                                  message_id=str(message_id) if message_id is not None else None)
        # Only an explicit protocol failure is a known rejection; async/malformed
        # responses do not prove that a visible message was not delivered.
        status = DeliveryStatus.REJECTED if data.get("status") == "failed" else DeliveryStatus.UNKNOWN
        wording = str(data.get("wording") or data.get("message") or "OneBot 未返回成功确认")
        token = self.config.onebot_access_token.strip()
        if token:
            wording = wording.replace(token, "[已隐藏]")
        wording = re.sub(r"https?://\S+|wss?://\S+", "[地址已隐藏]", wording)
        wording = re.sub(r"(?i)Bearer\s+\S+", "Bearer [已隐藏]", wording)
        result = DeliveryResult(status=status, transport=transport,
                                error_code=str(data["retcode"]) if "retcode" in data else None,
                                error=wording[:500])
        self._last_error = result.error
        logger.warning("OneBot delivery %s: %s", result.status.value, result.error)
        return result

    async def test_http_connection(self) -> dict:
        url = f"{self.config.onebot_http_url.rstrip('/')}/get_status"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.config.onebot_probe_timeout_seconds, headers=self._auth_headers(), trust_env=False) as client:
                response = await client.get(url)
                data = response.json()
            return {
                "success": response.is_success and self._response_ok(data),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "retcode": data.get("retcode"),
                "wording": data.get("wording"),
            }
        except Exception as error:
            return {
                "success": False,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "wording": str(error),
            }

    def _action_payload(self, action: ActionItem) -> tuple[str, dict]:
        endpoint = "send_group_msg" if action.action_type == ActionType.SEND_GROUP_MESSAGE else "send_private_msg"
        parts = [{"type": "reply", "data": {"id": str(action.reply_to)}}] if action.reply_to else []
        for segment in action.segments:
            if segment.type == "text":
                parts.append({"type": "text", "data": {"text": segment.text}})
            elif segment.type == "at_all":
                parts.append({"type": "at", "data": {"qq": "all"}})
            elif segment.type == "at":
                parts.append({"type": "at", "data": {"qq": segment.qq_uid}})
            elif segment.type == "image":
                if segment.asset_id not in action.resolved_images:
                    raise ValueError("Image asset has not been resolved by the runtime")
                data = {"file": action.resolved_images[segment.asset_id]}
                if segment.asset_id in action.resolved_sticker_ids:
                    data.update(sub_type=1, summary="[表情]")
                parts.append({"type": "image", "data": data})
        params = {"message": parts}
        target_id = action.scene_id.split(":")[-1]
        if action.action_type == ActionType.SEND_GROUP_MESSAGE:
            params["group_id"] = int(target_id)
        else:
            params["user_id"] = int(target_id)
        return endpoint, params

    @staticmethod
    def _response_ok(data: dict) -> bool:
        return data.get("status") == "ok" and data.get("retcode", 0) == 0

    def _remember_own_message(self, response: dict) -> None:
        message_id = (response.get("data") or {}).get("message_id")
        if message_id is not None:
            self._own_message_ids.append(str(message_id))

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(error)

    async def _handle_connection(self, websocket) -> None:
        if not self._is_authorized(websocket):
            logger.warning("Rejected unauthorized OneBot reverse WebSocket client")
            await websocket.close(code=1008, reason="Unauthorized")
            return
        if self._active_ws and self._active_ws is not websocket:
            await self._active_ws.close(code=1012, reason="Replaced by new OneBot connection")
        await self._consume_connection(websocket)

    async def _consume_connection(self, websocket) -> None:
        logger.info("OneBot WebSocket connected: %s", websocket.remote_address)
        self._active_ws = websocket
        try:
            async for raw_msg in websocket:
                try:
                    data = json.loads(raw_msg)
                    # Check if response to our sent action
                    echo = data.get("echo")
                    if echo and echo in self._pending_requests:
                        future = self._pending_requests[echo]
                        if not future.done():
                            future.set_result(data)
                        continue

                    # Otherwise process inbound event
                    event = self._normalize_event(data)
                    if event:
                        await self.on_event(event)
                except Exception as e:
                    logger.exception("Error processing OneBot message: %s", e)
        finally:
            if self._active_ws == websocket:
                self._active_ws = None
                self._fail_pending(ConnectionError("OneBot WebSocket disconnected"))
            logger.info("OneBot WebSocket disconnected")

    def _normalize_event(self, data: dict) -> Optional[Event]:
        self._observe_self_id(data)
        post_type = data.get("post_type")
        if post_type != "message":
            return None

        user_id = data.get("user_id")
        # ADR-0031, §17: drop bot's self-sent echo messages
        if user_id and str(user_id) == str(self.config.bot_qq):
            logger.debug("Dropped self-echo OneBot message from bot_qq %s", user_id)
            return None
        if data.get("sub_type") == "self":
            return None

        msg_type = data.get("message_type")
        raw_text = data.get("raw_message", "")
        if not raw_text and isinstance(data.get("message"), list):
            parts = []
            for segment in data["message"]:
                if not isinstance(segment, dict):
                    continue
                kind, detail = segment.get("type"), segment.get("data", {})
                if kind == "text":
                    parts.append(str(detail.get("text", "")))
                elif kind == "at":
                    parts.append(f"[CQ:at,qq={detail.get('qq', '')}]")
                elif kind == "reply":
                    parts.append(f"[CQ:reply,id={detail.get('id', '')}]")
                else:
                    parts.append("[图片]" if kind == "image" else f"[{kind}]")
            raw_text = "".join(parts)
        msg_time = float(data.get("time", time.time()))

        if msg_type == "group":
            scene_id = f"group:{data.get('group_id')}"
            etype = EventType.GROUP_MESSAGE_RECEIVED
        elif msg_type == "private":
            scene_id = f"private:{user_id}"
            etype = EventType.PRIVATE_MESSAGE_RECEIVED
        else:
            return None

        at_bot = (
            f"[CQ:at,qq={self.config.bot_qq}]" in raw_text
            or any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(self.config.bot_qq)
                   for s in data.get("message", []) if isinstance(s, dict))
        )

        # ADR-0031, §17: Extract reply_to_message_id (reply-bot detection; quote
        # replies use the OneBot message id directly exposed in cognition context).
        reply_to_id = None
        reply_match = re.search(r"\[CQ:reply,id=(-?\d+)\]", raw_text)
        if reply_match:
            reply_to_id = reply_match.group(1)
        elif isinstance(data.get("message"), list):
            for seg in data["message"]:
                if isinstance(seg, dict) and seg.get("type") == "reply":
                    reply_to_id = str(seg.get("data", {}).get("id", ""))
                    break

        reply_bot = bool(reply_to_id and str(reply_to_id) in self._own_message_ids)
        msg_id = data.get("message_id")

        # Person Context (ADR-0019 §十一): keep a sender snapshot for the person card.
        sender_raw = data.get("sender") or {}
        sender_snapshot = {
            "nickname": sender_raw.get("nickname"),
            "card": sender_raw.get("card") or None,
            "role": sender_raw.get("role")
        }

        return Event(
            event_type=etype,
            scene_id=scene_id,
            actor_id=f"user:{user_id}",
            timestamp=msg_time,
            payload={
                "message_id": msg_id,
                "reply_to_message_id": reply_to_id,
                "raw_text": raw_text,
                "at_bot": at_bot,
                "reply_bot": reply_bot,
                "sender": sender_snapshot,
                "segments": data.get("message") if isinstance(data.get("message"), list) else None,
            }
        )

    def _observe_self_id(self, data: dict) -> None:
        raw_self_id = data.get("self_id")
        if raw_self_id is None:
            return
        try:
            self_id = int(raw_self_id)
        except (TypeError, ValueError):
            return
        if self_id <= 0 or self_id == self._self_id:
            return

        previous = self._self_id
        self._self_id = self_id
        self.config.bot_qq = self_id
        if self.on_self_id:
            self.on_self_id(self_id)
        logger.info("OneBot self identity detected: %s (previous: %s)", self_id, previous)
