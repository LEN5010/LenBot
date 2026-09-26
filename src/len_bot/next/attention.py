"""Direct wake bursts and a single scene executor, backed by persisted inputs."""

import asyncio
import time
from collections.abc import Callable

from .chat import Chat
from .messages import ChatMessage, parse_message


def is_direct(message: ChatMessage) -> bool:
    return not message.is_self and (message.scene.startswith("private:") or message.mentions_bot)


class SceneRunner:
    def __init__(self, chat: Chat, emit: Callable[[dict], None], *, resume: bool):
        self.chat = chat
        self.store, self.config = chat.store, chat.config
        self.emit = emit
        self.changed = asyncio.Event()
        self.closing = False
        self.resume = resume
        self.own_ids = self.store.own_ids(self.config.scene)

    def receive(self, raw: dict) -> dict:
        message = parse_message(raw, own_message_ids=self.own_ids)
        if message.scene != self.config.scene or str(raw["self_id"]) != self.config.bot_qq:
            raise ValueError("输入场景或 Bot QQ 与隔离实例配置不同")
        if self.store.find_message(message.scene, message.platform_message_id) is not None:
            return {"status": "duplicate", "platform_message_id": message.platform_message_id}
        self.store.enqueue(message, raw, time.time())
        if message.is_self:
            self.own_ids.add(message.platform_message_id)
        self.changed.set()
        return {"status": "queued" if is_direct(message) else "stored",
                "platform_message_id": message.platform_message_id}

    def close_input(self) -> None:
        self.closing = True
        self.changed.set()

    def deadline(self, pending: list[tuple[int, ChatMessage, float]]) -> float | None:
        direct = [received for _, message, received in pending if is_direct(message)]
        if not direct:
            return None
        return min(pending[-1][2] + self.config.attention.direct_idle_seconds,
                   direct[0] + self.config.attention.direct_max_seconds)

    async def ready_messages(self, *, continuing: bool) -> list[tuple[int, ChatMessage, float]]:
        while True:
            self.changed.clear()
            pending = self.store.pending_messages(self.config.scene)
            deadline = self.deadline(pending)
            if deadline is None:
                return pending if continuing else []
            delay = deadline - time.time()
            if delay <= 0:
                return pending
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                # Expiration is the burst deadline, not a failed model request.
                pass

    def batch(self, pending: list[tuple[int, ChatMessage, float]], reason: str) -> tuple[int, str]:
        lines = [reason]
        for _, message, _ in pending:
            lines.append(self.chat.render(message) + f"（平台消息 ID：{message.platform_message_id}）")
        return pending[-1][0], "\n".join(lines)

    async def append_during_turn(self, continuing: bool, turn_id: str) -> bool:
        pending = await self.ready_messages(continuing=continuing)
        if not pending:
            return False
        through, content = self.batch(pending, "[思考期间收到新消息]")
        self.store.append_batch(self.config.scene, through, content, turn_id=turn_id)
        return True

    async def run(self) -> None:
        # Only this executor appends batches and runs the scene's model loop.
        while True:
            pending = await self.ready_messages(continuing=self.resume)
            if pending or self.resume:
                reason = "[恢复未结束的对话]" if self.resume else "[直接唤醒：被 @、被回复或私聊]"
                batch = self.batch(pending, reason) if pending else None
                self.resume = False
                result = await self.chat.run_turn(batch=batch, append_new=self.append_during_turn)
                self.resume = result["pending_wake"]
                self.emit(result)
                continue
            if self.closing:
                return
            await self.changed.wait()
