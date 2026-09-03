import asyncio
import json
import logging
import time
from typing import Optional, Callable, Awaitable
import websockets
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.actions.models import ActionItem, ActionType

logger = logging.getLogger(__name__)

class OneBotAdapter:
    def __init__(self, config: RuntimeConfig, on_event: Callable[[Event], Awaitable[None]]):
        self.config = config
        self.on_event = on_event
        self._server = None
        self._active_ws: Optional[websockets.WebSocketServerProtocol] = None
        self._echo_counter = 0
        self._pending_requests: dict[str, asyncio.Future[dict]] = {}

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle_connection,
            self.config.ws_host,
            self.config.ws_port
        )
        logger.info("OneBot Reverse WebSocket server started on ws://%s:%s", self.config.ws_host, self.config.ws_port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send_action(self, action: ActionItem) -> bool:
        """Sends action to connected OneBot client via JSON-RPC."""
        if not self._active_ws:
            logger.warning("Cannot send action: No OneBot client connected")
            return False

        self._echo_counter += 1
        echo = f"echo_{self._echo_counter}"
        
        endpoint = "send_group_msg" if action.action_type == ActionType.SEND_GROUP_MESSAGE else "send_private_msg"
        params = {"message": action.content}
        
        target_id = action.scene_id.split(":")[-1]
        if action.action_type == ActionType.SEND_GROUP_MESSAGE:
            params["group_id"] = int(target_id)
        else:
            params["user_id"] = int(target_id)

        payload = {
            "action": endpoint,
            "params": params,
            "echo": echo
        }

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[echo] = fut

        try:
            await self._active_ws.send(json.dumps(payload))
            res = await asyncio.wait_for(fut, timeout=10.0)
            return res.get("status") == "ok"
        except Exception as e:
            logger.error("Failed sending OneBot action: %s", e)
            return False
        finally:
            self._pending_requests.pop(echo, None)

    async def _handle_connection(self, websocket) -> None:
        logger.info("OneBot client connected: %s", websocket.remote_address)
        self._active_ws = websocket
        try:
            async for raw_msg in websocket:
                try:
                    data = json.loads(raw_msg)
                    # Check if response to our sent action
                    echo = data.get("echo")
                    if echo and echo in self._pending_requests:
                        self._pending_requests[echo].set_result(data)
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
            logger.info("OneBot client disconnected")

    def _normalize_event(self, data: dict) -> Optional[Event]:
        post_type = data.get("post_type")
        if post_type != "message":
            return None

        msg_type = data.get("message_type")
        user_id = data.get("user_id")
        raw_text = data.get("raw_message", "")
        msg_time = float(data.get("time", time.time()))

        if msg_type == "group":
            scene_id = f"group:{data.get('group_id')}"
            etype = EventType.GROUP_MESSAGE_RECEIVED
        elif msg_type == "private":
            scene_id = f"private:{user_id}"
            etype = EventType.PRIVATE_MESSAGE_RECEIVED
        else:
            return None

        # Historical Ingestion Gate check (ADR-0008)
        now = time.time()
        if (now - msg_time) > self.config.max_ingest_lag_seconds:
            etype = EventType.HISTORICAL_IMPORT

        at_bot = (
            f"[CQ:at,qq={self.config.bot_qq}]" in raw_text
            or any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(self.config.bot_qq)
                   for s in data.get("message", []) if isinstance(s, dict))
        )

        reply_bot = (
            "[CQ:reply" in raw_text and f"qq={self.config.bot_qq}" in raw_text
        )

        return Event(
            event_type=etype,
            scene_id=scene_id,
            actor_id=f"user:{user_id}",
            timestamp=msg_time,
            payload={
                "message_id": data.get("message_id"),
                "raw_text": raw_text,
                "at_bot": at_bot,
                "reply_bot": reply_bot
            }
        )
