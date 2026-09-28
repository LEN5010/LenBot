"""Scene wake selection, burst timing and persisted pending opportunities."""

import asyncio
import copy
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from .chat import Chat
from .config import Attention
from .delivery import report_parts, split_expression
from .messages import ChatMessage, Segment, parse_message, plain_text
from .quiet import next_quiet_start, quiet_period
from .schedule import check_creation, platform_role, wake_text


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
    quiet_notice_until: float | None = None

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
    def __init__(self, chat: Chat, emit: Callable[[dict], None], *, resume: bool,
                 ready_for_turn: Callable[[bool], Awaitable[bool]] | None = None):
        self.chat = chat
        self.now = chat.now
        self.store, self.config = chat.store, chat.config
        self.settings = self.config.attention
        self.emit = emit
        self.changed = asyncio.Event()
        self.closing = False
        self.resume = resume
        self.ready_for_turn = ready_for_turn
        self.own_ids = self.store.own_ids(self.config.scene)
        self.keywords = tuple(dict.fromkeys(word.strip().casefold() for word in
            [chat.persona.name, *chat.persona.aliases, *self.config.persona_aliases, *self.settings.keywords]))
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

    def clear_quiet_wake(self, state: AttentionState, now: float, period: tuple[float, float] | None) -> None:
        wake = state.pending
        if wake is not None and wake.channel != "direct":
            start = next_quiet_start(self.settings.quiet_hours, self.config.timezone, wake.first_at)
            if period is not None or (start is not None and start <= now):
                state.pending = None

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
        if quiet_period(self.settings.quiet_hours, self.config.timezone, at) is not None:
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

    def receive(self, raw: dict, *, ignore_other_scenes: bool = False) -> dict:
        message = parse_message(raw, own_message_ids=set())
        return self.receive_message(message, raw, ignore_other_scenes=ignore_other_scenes)

    def receive_message(self, message: ChatMessage, raw: dict, *, ignore_other_scenes: bool = False) -> dict:
        """Accept an already parsed message; own replies remain scoped to this scene."""
        if str(raw["self_id"]) != self.config.bot_qq:
            raise ValueError("输入场景或 Bot QQ 与隔离实例配置不同")
        if message.scene != self.config.scene:
            if ignore_other_scenes:
                return {"status": "ignored", "scene": message.scene,
                        "platform_message_id": message.platform_message_id}
            raise ValueError("输入场景或 Bot QQ 与隔离实例配置不同")
        if message.reply_to is not None:
            if message.reply_to in self.own_ids:
                message.mentions_bot = True
            else:
                # A send receipt may arrive without an own-message event.
                replied = self.store.find_message(message.scene, message.reply_to)
                if replied is not None and replied.is_self:
                    self.own_ids.add(message.reply_to)
                    message.mentions_bot = True
        existing = self.store.find_message(message.scene, message.platform_message_id)
        if existing is not None:
            if existing.is_self and existing.send_status == "sent":
                self.store.attach_echo(message, raw, self.now())
            return {"status": "duplicate", "platform_message_id": message.platform_message_id}
        now = self.now()
        state = copy.deepcopy(self.state)
        period = quiet_period(self.settings.quiet_hours, self.config.timezone, now)
        self.clear_quiet_wake(state, now, period)
        # Batch statistics are only needed for a new, non-direct ambient opportunity.
        pending, recent = [], []
        if (not is_direct(message) and not message.is_self and not self.settings.only_direct
                and message.sender.uid not in self.settings.other_bot_qqs and state.pending is None
                and self.settings.activity > 0 and period is None):
            pending = self.store.pending_attention_sample(self.config.scene, self.settings.other_bot_qqs, limit=19)
            pending.append((message, now))
            recent = self.store.attention_sample(self.config.scene, limit=19,
                                               exclude_uids=self.settings.other_bot_qqs) + [(message, now)]
        self.offer_message(state, message, now, pending, recent)
        snapshot = asdict(state) if state != self.state else None
        self.store.enqueue(
            message, raw, now, attention_state=snapshot,
            collect_stickers=(self.config.learning is not None and self.config.learning.collect_stickers
                              and not message.is_self and message.sender.uid != self.config.bot_qq
                              and message.sender.uid not in self.settings.other_bot_qqs),
        )
        self.state = state
        if message.is_self:
            self.own_ids.add(message.platform_message_id)
        self.changed.set()
        receipt = {"status": "queued" if state.pending else "stored",
                   "platform_message_id": message.platform_message_id,
                   "wake_channel": state.pending.channel if state.pending else None}
        if period is not None:
            receipt["quiet_until"] = datetime.fromtimestamp(period[1], ZoneInfo(self.config.timezone)).isoformat()
        return receipt

    def close_input(self) -> None:
        self.closing = True
        self.changed.set()

    async def wait_for_messages(self, seconds: float) -> str:
        started = time.monotonic()
        deadline = started + seconds
        while True:
            self.changed.clear()
            now = self.now()
            period = quiet_period(self.settings.quiet_hours, self.config.timezone, now)
            if period is None:
                available = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_qqs) is not None
            else:
                available = (self.settings.quiet_hours.direct == "allow" and self.state.pending is not None
                             and self.state.pending.channel == "direct")
            if available:
                reason = "收到新消息"
                break
            due_at = self.store.next_schedule_at(self.config.scene)
            if period is None and due_at is not None and due_at <= now:
                reason = "当前场景有到期安排，完整工具组结束后处理"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "已到等待时间"
                break
            delay = remaining if period is None else min(remaining, period[1] - now)
            if period is None and due_at is not None:
                delay = min(delay, due_at - now)
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                pass  # Recheck the wait deadline and any quiet interval that just ended.
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

    def due_schedules(self, now: float) -> list[tuple[int, str]]:
        scheduled = []
        for item in self.store.due_schedules(self.config.scene, now):
            try:
                if "schedule" not in self.chat.allowed_tool_names:
                    raise PermissionError("当前角色或场景未开放 schedule，安排未交付")
                check_creation(self.config.schedules, requester=item.requester, target=item.target,
                               bot_qq=self.config.bot_qq,
                               group_role=platform_role(self.store, self.config, item.requester))
            except PermissionError as error:
                reason = f"{type(error).__name__}: {error}"
                self.store.block_schedule(self.config.scene, item.id, reason)
                self.emit({"type": "schedule", "id": item.id, "status": "blocked", "error": reason})
            else:
                scheduled.append((item.id, wake_text(item, now)))
        return scheduled

    def schedule_deadline(self, now: float) -> float | None:
        due_at = self.store.next_schedule_at(self.config.scene)
        if self.chat.tasks is not None and self.chat.tasks.records.pending_notices(self.config.scene):
            due_at = now if due_at is None else min(now, due_at)
        if due_at is None:
            return None
        period = quiet_period(self.settings.quiet_hours, self.config.timezone, max(now, due_at))
        return due_at if period is None else period[1]

    async def ready_messages(self, *, continuing: bool = False, in_turn: bool = False
                             ) -> tuple[list[tuple[int, ChatMessage, float]], float | None,
                                        list[tuple[int, str]]] | None:
        while True:
            self.changed.clear()
            now = self.now()
            period = quiet_period(self.settings.quiet_hours, self.config.timezone, now)
            state = copy.deepcopy(self.state)
            self.clear_quiet_wake(state, now, period)
            self.save_state(state)
            scheduled = self.due_schedules(now) if period is None else []
            task_notice = (period is None and self.chat.tasks is not None
                           and self.chat.tasks.records.pending_notices(self.config.scene))
            if scheduled or task_notice:
                return self.store.pending_messages(self.config.scene), None, scheduled
            notice_until = None
            resuming = self.resume and not in_turn
            if period is not None:
                direct = self.state.pending is not None
                mode = self.settings.quiet_hours.direct
                if direct and mode in {"allow", "notice"}:
                    if mode == "notice":
                        if in_turn:
                            return None
                        notice_until = period[1]
                elif direct or resuming:
                    if in_turn or self.closing:
                        return None
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout=period[1] - now)
                    except TimeoutError:
                        pass  # Recheck the configured quiet boundary, not a model retry.
                    continue
                else:
                    return None
            if (continuing or resuming) and notice_until is None:
                pending = self.store.pending_messages(self.config.scene)
                return (pending, None, []) if pending or resuming else None
            deadline = self.deadline()
            if deadline is None:
                return None
            if self.state.pending.channel != "direct":
                quiet_start = next_quiet_start(self.settings.quiet_hours, self.config.timezone,
                                               self.state.pending.first_at)
                if quiet_start is not None:
                    deadline = min(deadline, quiet_start)
            schedule_at = self.schedule_deadline(now)
            if schedule_at is not None:
                deadline = min(deadline, schedule_at)
            delay = deadline - now
            if delay <= 0:
                return self.store.pending_messages(self.config.scene), notice_until, []
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                pass  # The known burst/cooldown deadline has arrived.

    def batch(self, pending: list[tuple[int, ChatMessage, float]], reason: str) -> tuple[int, list[str]]:
        contents = []
        for _, message, _ in pending:
            content = self.chat.render(message) + f"（平台消息 ID：{message.platform_message_id}）"
            if message.reply_to is not None:
                content += f"（回复平台消息 ID：{message.reply_to}）"
            contents.append(content)
        contents[0] = reason + "\n" + contents[0]
        return pending[-1][0], contents

    def consumed_state(self) -> AttentionState:
        state = copy.deepcopy(self.state)
        if state.pending is not None:
            if state.pending.keywords:
                state.keyword_last.update(dict.fromkeys(state.pending.keywords, self.now()))
            if state.pending.channel == "ambient":
                state.ambient_last_at = self.now()
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
        if self.ready_for_turn is not None and not await self.ready_for_turn(False):
            return False
        state = copy.deepcopy(self.state)
        own_at = self.store.last_self_time(self.config.scene)
        if own_at is not None:
            state.contact(own_at, self.settings.focus_seconds)
        self.save_state(state)
        ready = await self.ready_messages(continuing=continuing, in_turn=True)
        if ready is None:
            return False
        if self.ready_for_turn is not None and not await self.ready_for_turn(False):
            return False
        pending, _, scheduled = ready
        state = self.consumed_state()
        if pending:
            through, contents = self.batch(pending, self.wake_reason())
            self.store.append_batch(self.config.scene, through, contents, turn_id=turn_id,
                                    attention_state=asdict(state))
            if self.state.pending is not None and self.state.pending.channel == "direct":
                self.chat.direct_request = True
        if scheduled:
            self.store.append_schedules(self.config.scene, scheduled, turn_id=turn_id)
        if self.chat.tasks is not None and quiet_period(
                self.settings.quiet_hours, self.config.timezone, self.now()) is None:
            notices = self.chat.tasks.records.pending_notices(self.config.scene)
            if notices:
                self.store.append_task_notices(self.config.scene, notices, turn_id=turn_id)
        self.state = state
        return True

    async def quiet_notice(self, pending: list[tuple[int, ChatMessage, float]], until: float) -> None:
        state = self.consumed_state()
        parts, note = None, None
        expressions = []
        prefix = ("[宿主安静时段固定表达；模拟，未发送到 QQ]\n" if self.chat.send_message is None
                  else "[宿主安静时段固定表达]\n")
        if state.quiet_notice_until != until:
            expression = self.chat.simulated_message([
                Segment("text", {"text": self.settings.quiet_hours.notice_text})])
            parts = split_expression(expression, self.config.text_delivery.max_chars)
            note = prefix + report_parts(parts, [], self.chat.render)
            state.quiet_notice_until = until
        entry_seq = self.store.append_quiet(
            self.config.scene, self.batch(pending, "[安静时段直接消息；本批未调用模型]"),
            attention_state=asdict(state), note=note,
        )
        self.state = state
        status, error_text = "stored", None
        if parts is not None:
            try:
                async with asyncio.timeout(self.config.turn_timeout_seconds):
                    content, status = await self.chat.send_prepared_expression(entry_seq, parts, prefix=prefix)
            except TimeoutError as error:
                status, error_text = "timeout", f"{type(error).__name__}: fixed notice time limit"
                content = self.store.expression_error(entry_seq, error_text)
            expressions.append(content)
            # Receipt callbacks may have persisted newer attention while sending.
            state = copy.deepcopy(self.state)
            own_at = self.store.last_self_time(self.config.scene)
            if own_at is not None:
                state.contact(own_at, self.settings.focus_seconds)
            self.save_state(state)
        delivery = "none" if parts is None else "simulated" if self.chat.send_message is None else "onebot"
        self.emit({"type": "notice", "status": status, "error": error_text,
                   "delivery": delivery, "expressions": expressions,
                   "quiet_until": datetime.fromtimestamp(until, ZoneInfo(self.config.timezone)).isoformat()})

    async def run(self) -> None:
        while True:
            if self.ready_for_turn is not None and not await self.ready_for_turn(True):
                return
            ready = await self.ready_messages()
            if ready is not None:
                # Merging may await beyond a disconnect. Re-enter admission without
                # consuming this snapshot, rather than resume it after a reconnect.
                if self.ready_for_turn is not None and not await self.ready_for_turn(False):
                    continue
                pending, notice_until, scheduled = ready
                if notice_until is not None:
                    await self.quiet_notice(pending, notice_until)
                    continue
                notices = (self.chat.tasks.records.pending_notices(self.config.scene)
                           if self.chat.tasks is not None and quiet_period(
                               self.settings.quiet_hours, self.config.timezone, self.now()) is None else [])
                channel = "system" if scheduled or notices else self.state.pending.channel if self.state.pending else "resume"
                reason = "[恢复未结束的对话]" if self.resume else self.wake_reason()
                batch = self.batch(pending, reason) if pending else None
                direct = self.state.pending is not None and self.state.pending.channel == "direct"
                wake_received_at = (self.state.pending.first_at
                                    if not scheduled and self.state.pending is not None else None)
                state = self.consumed_state()
                contact_before = state.last_contact_at
                self.state, self.resume = state, False
                result = await self.chat.run_turn(batch=batch, append_new=self.append_during_turn,
                                                  wait_for_messages=self.wait_for_messages,
                                                  attention_state=asdict(state), scheduled=scheduled,
                                                  task_notices=notices,
                                                  direct=direct, wake_received_at=wake_received_at)
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
                # Preserve restart eligibility without treating a timeout as new input.
                pending_wake = self.store.end_turn(result["turn_id"], result["status"], result["error"],
                                                   attention_state=asdict(state))
                self.state = state
                result["pending_wake"] = pending_wake
                self.emit(result)
                continue
            if self.closing:
                return
            deadline = self.schedule_deadline(self.now())
            if deadline is None:
                await self.changed.wait()
            else:
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=max(0, deadline - self.now()))
                except TimeoutError:
                    pass  # Recheck the actual due time and quiet interval.
