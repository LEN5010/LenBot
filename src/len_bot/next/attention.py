"""Scene wake selection, burst timing and persisted pending opportunities."""

import asyncio
import copy
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from .chat import Chat
from .config import Attention
from .messages import ChatMessage, parse_message, plain_text


Channel = Literal["direct", "named", "focus", "ambient"]
PRIORITY = {"direct": 0, "named": 1, "focus": 2, "ambient": 3}
REACTIONS = {"哈哈", "哈哈哈", "草", "6", "笑死", "啊", "哦", "嗯", ""}
HELP = re.compile(r"(?<!不)怎么|如何|为什么|有没有|帮我|帮忙|你们觉得")


@dataclass
class PendingWake:
    channel: Channel
    first_at: float
    keywords: list[str] = field(default_factory=list)
    score: float | None = None


@dataclass
class AttentionState:
    focus_started_at: float | None = None
    last_contact_at: float | None = None
    ambient_last_at: float | None = None
    silence_level: int = 0
    keyword_last: dict[str, float] = field(default_factory=dict)
    pending: PendingWake | None = None

    def contact(self, at: float, duration: float) -> None:
        if self.last_contact_at is None or at > self.last_contact_at:
            if self.last_contact_at is None or at > self.last_contact_at + duration:
                self.focus_started_at = at
            self.last_contact_at = at
            self.silence_level = 0

    def offer(self, wake: PendingWake) -> None:
        if self.pending is None:
            self.pending = wake
            return
        words = sorted(set(self.pending.keywords + wake.keywords))
        if PRIORITY[wake.channel] < PRIORITY[self.pending.channel]:
            self.pending = wake
        elif wake.channel == self.pending.channel:
            self.pending.first_at = min(self.pending.first_at, wake.first_at)
        self.pending.keywords = words


def is_direct(message: ChatMessage) -> bool:
    return not message.is_self and (message.scene.startswith("private:") or message.mentions_bot)


def participation_score(pending: list[tuple[ChatMessage, float]],
                        recent: list[tuple[ChatMessage, float]], config: Attention) -> float:
    humans = [(message, at) for message, at in pending
              if not message.is_self and message.sender.uid not in config.other_bot_qqs]
    if not humans:
        return 0.0
    texts = [plain_text(message).strip() for message, _ in humans]
    substantial = [text for text in texts if text.strip(" ?？!！。~") not in REACTIONS]
    score = 0.2 + min(len(humans) / 10, 2.0)
    if any(("?" in text or "？" in text) and len(re.sub(r"[\W_]", "", text)) >= 3
           for text in substantial):
        score += 1.0
    if any(HELP.search(text) for text in substantial):
        score += 1.0
    if not substantial:
        score -= 0.8
    if not any(texts):
        score -= 0.7
    other_targets = sum(any(segment.type == "at" and str(segment.data["qq"]) in config.other_bot_qqs
                            for segment in message.segments) for message, _ in humans)
    score -= other_targets / len(humans)
    sample = [(message, at) for message, at in recent
              if message.is_self or message.sender.uid not in config.other_bot_qqs]
    if sample:
        score -= 1.5 * sum(message.is_self and message.send_status != "failed"
                           for message, _ in sample) / len(sample)
    times = [at for message, at in sample if not message.is_self]
    if len(times) >= 3:
        prior_gaps = [later - earlier for earlier, later in zip(times[:-2], times[1:-1])]
        if times[-1] - times[-2] > sum(prior_gaps) / len(prior_gaps):
            score += 0.5
    return max(0.0, score) * config.activity


class SceneRunner:
    def __init__(self, chat: Chat, emit: Callable[[dict], None], *, resume: bool):
        self.chat = chat
        self.store, self.config = chat.store, chat.config
        self.settings = self.config.attention
        self.emit = emit
        self.changed = asyncio.Event()
        self.closing = False
        self.resume = resume
        self.own_ids = self.store.own_ids(self.config.scene)
        self.keywords = tuple(dict.fromkeys(word.strip().casefold() for word in
            [chat.persona.name, *chat.persona.aliases, *self.settings.keywords]))
        saved = self.store.load_attention(self.config.scene)
        if saved is None:
            self.state = AttentionState()
        else:
            if saved["pending"] is not None:
                saved["pending"] = PendingWake(**saved["pending"])
            self.state = AttentionState(**saved)
        restored = copy.deepcopy(self.state)
        if restored.pending is not None:
            channel = restored.pending.channel
            disabled = (self.settings.only_direct and channel != "direct"
                        or channel == "ambient" and self.settings.activity == 0
                        or channel == "focus" and self.settings.focus_seconds == 0)
            eligible = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_qqs) is not None
            if disabled or not eligible:
                restored.pending = None
        own_at = self.store.last_self_time(self.config.scene)
        if own_at is not None:
            restored.contact(own_at, self.settings.focus_seconds)
        if saved is None:
            recent = self.store.attention_sample(self.config.scene, exclude_uids=self.settings.other_bot_qqs)
            seen = []
            for _, message, at in self.store.pending_messages(self.config.scene):
                if not message.is_self and message.sender.uid not in self.settings.other_bot_qqs:
                    seen = (seen + [(message, at)])[-20:]
                self.offer_message(restored, message, at, seen, recent)
            # An empty state also records that older input was already considered.
            self.store.save_attention(self.config.scene, asdict(restored))
            self.state = restored
        else:
            self.save_state(restored)

    def save_state(self, state: AttentionState) -> None:
        if state != self.state:
            self.store.save_attention(self.config.scene, asdict(state))
            self.state = state

    def offer_message(self, state: AttentionState, message: ChatMessage, at: float,
                      pending: list[tuple[ChatMessage, float]], recent: list[tuple[ChatMessage, float]]) -> None:
        if is_direct(message):
            state.contact(at, self.settings.focus_seconds)
            state.silence_level = 0
            state.offer(PendingWake("direct", at))
            return
        if message.is_self:
            if message.send_status in {"sent", "received", "simulated"}:
                state.contact(at, self.settings.focus_seconds)
            return
        if self.settings.only_direct or message.sender.uid in self.settings.other_bot_qqs:
            return
        text = plain_text(message).casefold()
        words = [word for word in self.keywords if word in text and
                 (word not in state.keyword_last or at >= state.keyword_last[word] + self.settings.keyword_cooldown_seconds)]
        if words:
            state.offer(PendingWake("named", at, words))
        elif (self.settings.focus_seconds > 0 and state.last_contact_at is not None
              and state.focus_started_at <= at <= state.last_contact_at + self.settings.focus_seconds):
            state.offer(PendingWake("focus", at))
        elif state.pending is None and self.settings.activity > 0:
            score = participation_score(pending, recent, self.settings)
            if score > self.settings.ambient_threshold:
                state.offer(PendingWake("ambient", at, score=score))

    def receive(self, raw: dict) -> dict:
        message = parse_message(raw, own_message_ids=self.own_ids)
        if message.scene != self.config.scene or str(raw["self_id"]) != self.config.bot_qq:
            raise ValueError("输入场景或 Bot QQ 与隔离实例配置不同")
        if self.store.find_message(message.scene, message.platform_message_id) is not None:
            return {"status": "duplicate", "platform_message_id": message.platform_message_id}
        now = time.time()
        state = copy.deepcopy(self.state)
        # Batch statistics are only needed for a new, non-direct ambient opportunity.
        pending, recent = [], []
        if (not is_direct(message) and not message.is_self and not self.settings.only_direct
                and message.sender.uid not in self.settings.other_bot_qqs and state.pending is None
                and self.settings.activity > 0):
            pending = self.store.pending_attention_sample(self.config.scene, self.settings.other_bot_qqs, limit=19)
            pending.append((message, now))
            recent = self.store.attention_sample(self.config.scene, limit=19,
                                               exclude_uids=self.settings.other_bot_qqs) + [(message, now)]
        self.offer_message(state, message, now, pending, recent)
        snapshot = asdict(state) if state != self.state else None
        self.store.enqueue(message, raw, now, attention_state=snapshot)
        self.state = state
        if message.is_self:
            self.own_ids.add(message.platform_message_id)
        self.changed.set()
        return {"status": "queued" if state.pending else "stored",
                "platform_message_id": message.platform_message_id,
                "wake_channel": state.pending.channel if state.pending else None}

    def close_input(self) -> None:
        self.closing = True
        self.changed.set()

    async def wait_for_messages(self, seconds: float) -> str:
        started = time.monotonic()
        deadline = started + seconds
        while True:
            self.changed.clear()
            if self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_qqs) is not None:
                reason = "收到新消息"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "已到等待时间"
                break
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=remaining)
            except TimeoutError:
                reason = "已到等待时间"
                break
        return f"等待结束：实际等待 {time.monotonic() - started:.3f} 秒；{reason}。"

    def ambient_interval(self) -> float:
        base, maximum = self.settings.ambient_min_interval_seconds, self.settings.ambient_max_interval_seconds
        cap = math.ceil(math.log2(maximum) - math.log2(base))
        return maximum if self.state.silence_level >= cap else math.ldexp(base, self.state.silence_level)

    def deadline(self) -> float | None:
        wake = self.state.pending
        if wake is None:
            return None
        if wake.channel == "ambient":
            return max(wake.first_at, wake.first_at if self.state.ambient_last_at is None else
                       self.state.ambient_last_at + self.ambient_interval())
        latest = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_qqs)
        idle = getattr(self.settings, wake.channel + "_idle_seconds")
        maximum = getattr(self.settings, wake.channel + "_max_seconds")
        return min((wake.first_at if latest is None else latest) + idle, wake.first_at + maximum)

    async def ready_messages(self, *, continuing: bool) -> list[tuple[int, ChatMessage, float]]:
        while True:
            self.changed.clear()
            if continuing:
                return self.store.pending_messages(self.config.scene)
            deadline = self.deadline()
            if deadline is None:
                return []
            delay = deadline - time.time()
            if delay <= 0:
                return self.store.pending_messages(self.config.scene)
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                pass  # The known burst/cooldown deadline has arrived.

    def batch(self, pending: list[tuple[int, ChatMessage, float]], reason: str) -> tuple[int, list[str]]:
        contents = [self.chat.render(message) + f"（平台消息 ID：{message.platform_message_id}）"
                    for _, message, _ in pending]
        contents[0] = reason + "\n" + contents[0]
        return pending[-1][0], contents

    def consumed_state(self) -> AttentionState:
        state = copy.deepcopy(self.state)
        if state.pending is not None:
            if state.pending.keywords:
                state.keyword_last.update(dict.fromkeys(state.pending.keywords, time.time()))
            if state.pending.channel == "ambient":
                state.ambient_last_at = time.time()
            state.pending = None
        return state

    def wake_reason(self) -> str:
        wake = self.state.pending
        if wake is None:
            return "[对话中收到新消息]"
        if wake.channel == "named":
            return "[点名：" + "、".join(wake.keywords) + "]"
        if wake.channel == "focus":
            return "[对话继续]"
        if wake.channel == "ambient":
            return f"[群里在聊；参与分 {wake.score:.3f}]"
        return "[直接唤醒：被 @、被回复或私聊]"

    async def append_during_turn(self, continuing: bool, turn_id: str) -> bool:
        state = copy.deepcopy(self.state)
        own_at = self.store.last_self_time(self.config.scene)
        if own_at is not None:
            state.contact(own_at, self.settings.focus_seconds)
        self.save_state(state)
        pending = await self.ready_messages(continuing=continuing)
        if not pending:
            return False
        through, contents = self.batch(pending, self.wake_reason())
        state = self.consumed_state()
        self.store.append_batch(self.config.scene, through, contents, turn_id=turn_id, attention_state=asdict(state))
        self.state = state
        return True

    async def run(self) -> None:
        while True:
            pending = await self.ready_messages(continuing=self.resume)
            if pending or self.resume:
                channel = self.state.pending.channel if self.state.pending else "resume"
                reason = "[恢复未结束的对话]" if self.resume else self.wake_reason()
                batch = self.batch(pending, reason) if pending else None
                state = self.consumed_state()
                contact_before = state.last_contact_at
                self.state, self.resume = state, False
                result = await self.chat.run_turn(batch=batch, append_new=self.append_during_turn,
                                                  wait_for_messages=self.wait_for_messages,
                                                  attention_state=asdict(state))
                state = copy.deepcopy(self.state)
                own_at = self.store.last_self_time(self.config.scene)
                if own_at is not None:
                    state.contact(own_at, self.settings.focus_seconds)
                if result["expressions"]:
                    state.silence_level = 0
                elif (channel in {"named", "focus", "ambient"} and result["status"] == "settled"
                      and result["failed_tools"] == 0 and state.last_contact_at == contact_before):
                    cap = math.ceil(math.log2(self.settings.ambient_max_interval_seconds)
                                    - math.log2(self.settings.ambient_min_interval_seconds))
                    state.silence_level = min(state.silence_level + 1, cap)
                self.resume = self.store.end_turn(result["turn_id"], result["status"], result["error"],
                                                  attention_state=asdict(state))
                self.state = state
                result["pending_wake"] = self.resume
                self.emit(result)
                continue
            if self.closing:
                return
            await self.changed.wait()
