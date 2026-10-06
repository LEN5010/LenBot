"""Scene wake selection, burst timing and persisted pending opportunities."""

import asyncio
import copy
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from string import Template
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from .session import Chat
from .recap import estimate_content, estimate_request
from .scene_control import SceneControlArguments, TemporaryQuiet, require_control
from ..models.limits import LimitReached
from ..configuration.chat import Attention
from ..platform.delivery import report_parts, split_expression
from ..platform.messages import ChatMessage, Segment, plain_text
from ..platform.onebot_messages import parse_message
from .proactive import PROMPT as PROACTIVE_PROMPT, ProactiveStore, idle_text
from .quiet import next_quiet_start, quiet_period
from .schedule import effective_settings, check_creation, platform_role, wake_text
from ..plugins.store import PluginStore
from .schedule_store import ScheduleStore


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
    limit_notice_until: float | None = None
    temporary_quiet: TemporaryQuiet | None = None
    # Operator switch from the panel: no turns until turned back on, without an end time.
    paused: bool = False

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
    return not message.is_self and (message.scene.split(":", 2)[1] == "private" or message.mentions_bot)


def participation_score(pending: list[tuple[ChatMessage, float]],
                        recent: list[tuple[ChatMessage, float]], config: Attention) -> float:
    humans = [(message, at) for message, at in pending
              if not message.is_self and message.sender.uid not in config.other_bot_ids]
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
    other_targets = sum(any(segment.type == "mention" and str(segment.data["user"]) in config.other_bot_ids
                            for segment in message.segments) for message, _ in humans)
    score -= other_targets / len(humans)
    sample = [(message, at) for message, at in recent
              if message.is_self or message.sender.uid not in config.other_bot_ids]
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
    def __init__(self, chat: Chat, emit: Callable[[dict], None], *,
                 ready_for_turn: Callable[[bool], Awaitable[bool]] | None = None,
                 connected_since: Callable[[], float | None] = lambda: None):
        self.execution = asyncio.Lock()
        self.chat = chat
        self.connected_since = connected_since
        self.started_at = chat.now()
        self.now = chat.now
        self.store, self.config = chat.store, chat.config
        self.settings = self.config.attention
        self.emit = emit
        self.changed = asyncio.Event()
        self.closing = False
        self.ready_for_turn = ready_for_turn
        self.own_ids = self.store.own_ids(self.config.scene)
        self.proactive = None if self.config.proactive is None else ProactiveStore(self.store)
        self.keywords = tuple(dict.fromkeys(word.strip().casefold() for word in
            [chat.persona.name, *chat.persona.aliases, *self.config.persona_aliases, *self.settings.keywords]))
        saved = self.store.load_attention(self.config.scene)
        if saved is None:
            self.state = AttentionState()
        else:
            if saved["pending"] is not None:
                saved["pending"] = PendingWake(**saved["pending"])
            if saved.get("temporary_quiet") is not None:
                saved["temporary_quiet"] = TemporaryQuiet(**saved["temporary_quiet"])
            self.state = AttentionState(**saved)
        restored = copy.deepcopy(self.state)
        if restored.pending is not None:
            channel = restored.pending.channel
            disabled = (self.settings.only_direct and channel != "direct"
                        or channel == "ambient" and self.settings.activity == 0
                        or channel == "focus" and self.settings.focus_seconds == 0)
            eligible = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_ids) is not None
            if disabled or not eligible:
                restored.pending = None
        own_at = self.store.last_self_time(self.config.scene)
        if own_at is not None:
            restored.contact(own_at, self.settings.focus_seconds)
        if saved is None:
            recent = self.store.attention_sample(self.config.scene, exclude_uids=self.settings.other_bot_ids)
            seen = []
            for _, message, at in self.store.pending_messages(self.config.scene):
                if not message.is_self and message.sender.uid not in self.settings.other_bot_ids:
                    seen = (seen + [(message, at)])[-20:]
                self.offer_message(restored, message, at, seen, recent)
            # An empty state also records that older input was already considered.
            self.store.save_attention(self.config.scene, asdict(restored))
            self.state = restored
        else:
            self.save_state(restored)

        self.chat.toolset.scene_control = self.control
        self.chat.set_external_tools(list(self.chat.toolset.external.values()))
        self.resume = self.chat.restore()

    def quiet_period(self, now: float) -> tuple[float, float] | None:
        if self.state.paused:
            return now, math.inf
        configured = quiet_period(self.settings.quiet_hours, self.config.timezone, now)
        temporary = self.state.temporary_quiet
        if temporary is None or not temporary.started <= now < temporary.until:
            return configured
        if configured is None:
            return temporary.started, temporary.until
        return min(configured[0], temporary.started), min(configured[1], temporary.until)

    def quiet_direct(self, now: float) -> str:
        if self.state.paused:
            return 'defer'
        configured = quiet_period(self.settings.quiet_hours, self.config.timezone, now)
        mode = 'allow' if configured is None else self.settings.quiet_hours.direct
        temporary = self.state.temporary_quiet
        if temporary is not None and temporary.started <= now < temporary.until and temporary.direct == 'defer':
            return 'defer'
        return mode

    def control_state(self) -> dict:
        now = self.now()
        period = self.quiet_period(now)
        return {'scene': self.config.scene, 'timezone': self.config.timezone,
                'attention': self.settings.model_dump(mode='json'),
                'temporary_quiet': None if self.state.temporary_quiet is None else asdict(self.state.temporary_quiet),
                'paused': self.state.paused,
                'quiet_until': None if period is None or self.state.paused else period[1],
                'direct': self.quiet_direct(now),
                'scope': Template((Path(__file__).resolve().parents[2] / 'prompts' / 'next_scene_control.md').read_text()).substitute(scene=self.config.scene).strip()}

    def set_temporary_quiet(self, seconds: int | None, direct: Literal["allow", "defer"], requester: str | None) -> dict:
        now = self.now()
        state = copy.deepcopy(self.state)
        state.temporary_quiet = (None if seconds is None else
                                 TemporaryQuiet(now, now + seconds, direct, requester))
        self.clear_quiet_wake(state, now, None if seconds is None else (now, now + seconds))
        self.save_state(state)
        self.changed.set()
        self.chat.notify()
        return self.control_state()

    def set_paused(self, paused: bool) -> dict:
        """Turn chat off or on for this scene; messages are still stored while off."""
        state = copy.deepcopy(self.state)
        state.paused = paused
        # Turning back on does not answer what was said while off; it stays as context.
        state.pending = None
        self.save_state(state)
        self.changed.set()
        self.chat.notify()
        return self.control_state()

    def control(self, args: SceneControlArguments) -> dict:
        if args.action == 'status':
            return self.control_state()
        require_control(self.store, self.config, args.requester)
        return self.set_temporary_quiet(args.seconds if args.action == 'quiet' else None,
                                        'allow' if args.direct is None else args.direct, args.requester)

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
        if self.settings.only_direct or message.sender.uid in self.settings.other_bot_ids:
            return
        if self.quiet_period(at) is not None:
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

    def receive_message(self, message: ChatMessage, raw: dict, *, ignore_other_scenes: bool = False,
                        wake: bool = True, plugin_claim: tuple[str, str] | None = None) -> dict:
        """Accept an already parsed message; own replies remain scoped to this scene.

        ``wake=False`` stores a plugin command without offering a wake; it still
        reaches the mind with the next batch.
        """
        if message.bot_id != self.config.bot_id:
            raise ValueError("输入场景或 Bot 账号与隔离实例配置不同")
        if message.scene != self.config.scene:
            if ignore_other_scenes:
                return {"status": "ignored", "scene": message.scene,
                        "platform_message_id": message.platform_message_id}
            raise ValueError("输入场景或 Bot 账号与隔离实例配置不同")
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
        blocked = message.sender.uid in self.config.permissions.blacklist
        wake = wake and not blocked
        now = self.now()
        state = copy.deepcopy(self.state)
        period = self.quiet_period(now)
        self.clear_quiet_wake(state, now, period)
        # Batch statistics are only needed for a new, non-direct ambient opportunity.
        pending, recent = [], []
        if (wake and not is_direct(message) and not message.is_self and not self.settings.only_direct
                and message.sender.uid not in self.settings.other_bot_ids and state.pending is None
                and self.settings.activity > 0 and period is None):
            pending = self.store.pending_attention_sample(self.config.scene, self.settings.other_bot_ids, limit=19)
            pending.append((message, now))
            recent = self.store.attention_sample(self.config.scene, limit=19,
                                               exclude_uids=self.settings.other_bot_ids) + [(message, now)]
        if wake:
            self.offer_message(state, message, now, pending, recent)
        snapshot = asdict(state) if state != self.state else None
        self.store.enqueue(
            message, raw, now, attention_state=snapshot,
            plugin_claim=plugin_claim,
            collect_stickers=(not blocked and self.config.learning is not None and self.config.learning.collect_stickers
                              and not message.is_self and message.sender.uid != self.config.bot_id
                              and message.sender.uid not in self.settings.other_bot_ids),
            transcribe_audio=(not blocked and self.config.transcribe_audio and not message.is_self
                              and message.sender.uid not in self.settings.other_bot_ids),
        )
        self.state = state
        if message.is_self:
            self.own_ids.add(message.platform_message_id)
        self.changed.set()
        receipt = {"status": "queued" if state.pending else "stored",
                   "platform_message_id": message.platform_message_id,
                   "wake_channel": state.pending.channel if state.pending else None}
        if blocked:
            receipt['reason'] = 'blacklisted: saved without wake or automatic media processing'
        if state.paused:
            receipt["paused"] = True
        elif period is not None:
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
            period = self.quiet_period(now)
            if period is None:
                available = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_ids) is not None
            else:
                available = (self.quiet_direct(now) == "allow" and self.state.pending is not None
                             and self.state.pending.channel == "direct")
            if available:
                reason = "收到新消息"
                break
            if period is None and self.audio_ready():
                reason = "此前语音的识别结果已就绪，完整工具组结束后处理"
                break
            due_at = ScheduleStore(self.store).next_schedule_at(self.config.scene)
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
        observed = datetime.fromtimestamp(self.now(), ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
        return f"等待结束 {observed}：实际等待 {time.monotonic() - started:.3f} 秒；{reason}。"

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
        latest = self.store.last_pending_arrival(self.config.scene, self.settings.other_bot_ids)
        idle = getattr(self.settings, wake.channel + "_idle_seconds")
        maximum = getattr(self.settings, wake.channel + "_max_seconds")
        return min((wake.first_at if latest is None else latest) + idle, wake.first_at + maximum)

    def due_schedules(self, now: float) -> list[tuple[int, str]]:
        scheduled = []
        for item in ScheduleStore(self.store).due_schedules(self.config.scene, now):
            try:
                if "schedule" not in self.chat.toolset.allowed_tool_names:
                    raise PermissionError("当前角色或场景未开放 schedule，安排未交付")
                if item.requester in self.config.permissions.blacklist:
                    raise PermissionError('安排请求人已在黑名单中')
                check_creation(effective_settings(self.config), requester=item.requester, target=item.target,
                               bot_id=self.config.bot_id, root_owners=self.config.owners,
                               group_role=platform_role(self.store, self.config, item.requester))
            except PermissionError as error:
                reason = f"{type(error).__name__}: {error}"
                ScheduleStore(self.store).block_schedule(self.config.scene, item.id, reason)
                self.emit({"type": "schedule", "id": item.id, "status": "blocked", "error": reason})
            else:
                scheduled.append((item.id, wake_text(item, now)))
        return scheduled

    def schedule_deadline(self, now: float) -> float | None:
        due_at = ScheduleStore(self.store).next_schedule_at(self.config.scene)
        if ((self.chat.tasks is not None and self.chat.tasks.records.pending_notices(self.config.scene))
                or PluginStore(self.store).plugin_wake_pending(self.config.scene) or self.audio_ready()):
            due_at = now if due_at is None else min(now, due_at)
        if due_at is None:
            return None
        period = self.quiet_period(max(now, due_at))
        return due_at if period is None else period[1]

    def audio_ready(self) -> bool:
        return self.chat.audio is not None and self.chat.audio.records.results_pending(self.config.scene)

    async def wait_audio(self, *, wait: bool = True) -> bool:
        if self.closing or self.chat.audio is None:
            return False
        remaining = self.chat.audio.wait_remaining(self.config.scene, self.now())
        if remaining <= 0:
            return False
        if not wait:
            return True
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=remaining)
        except TimeoutError:
            pass  # Stop holding the input batch at its configured media deadline.
        return True

    async def ready_messages(self, *, continuing: bool = False, in_turn: bool = False, wait: bool = True
                             ) -> tuple[list[tuple[int, ChatMessage, float]], float | None,
                                        list[tuple[int, str]]] | None:
        """Select current work; wait=False rechecks admission without burst/media waits."""
        while True:
            self.changed.clear()
            now = self.now()
            try:
                self.chat.check_limits(model=True)
            except LimitReached as error:
                if in_turn or self.closing or not wait:
                    return None
                quiet = self.quiet_period(now)
                if (self.state.pending is not None and self.state.pending.channel == "direct"
                        and (quiet is None or self.quiet_direct(now) != "defer")):
                    async with self.execution:
                        await self.limit_notice(error)
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=max(0, min(error.until, quiet[1] if quiet is not None else error.until) - self.now()))
                except TimeoutError:
                    pass  # The configured allowance window ended; no failed request is retried.
                continue
            period = self.quiet_period(now)
            state = copy.deepcopy(self.state)
            self.clear_quiet_wake(state, now, period)
            self.save_state(state)
            scheduled = self.due_schedules(now) if period is None else []
            task_notice = (period is None and self.chat.tasks is not None
                           and self.chat.tasks.records.pending_notices(self.config.scene))
            plugin_event = period is None and PluginStore(self.store).plugin_wake_pending(self.config.scene)
            if scheduled or task_notice or plugin_event or (period is None and self.audio_ready()):
                return self.store.pending_messages(self.config.scene), None, scheduled
            notice_until = None
            resuming = self.resume and not in_turn
            if period is not None:
                direct = self.state.pending is not None
                mode = self.quiet_direct(now)
                if direct and mode in {"allow", "notice"}:
                    if mode == "notice":
                        if in_turn:
                            return None
                        notice_until = period[1]
                elif direct or resuming:
                    if in_turn or self.closing or not wait:
                        return None
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout=period[1] - now)
                    except TimeoutError:
                        pass  # Recheck the configured quiet boundary, not a model retry.
                    continue
                else:
                    return None
            if (continuing or resuming) and notice_until is None:
                if await self.wait_audio(wait=wait):
                    if not wait:
                        return None
                    continue
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
                if notice_until is None and await self.wait_audio(wait=wait):
                    if not wait:
                        return None
                    continue
                return self.store.pending_messages(self.config.scene), notice_until, []
            if self.closing:
                # EOF drains accepted input without waiting for another arrival;
                # a stopped network host rejects new turns at admission.
                return self.store.pending_messages(self.config.scene), notice_until, []
            if not wait:
                return None
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                pass  # The known burst/cooldown deadline has arrived.

    def batch(self, pending: list[tuple[int, ChatMessage, float]], reason: str) -> tuple[int, list[str]]:
        binding = self.config.models.roles.mind
        available = (self.config.compaction.input_tokens
                     - estimate_request([{"role": "system", "content": self.chat.context.system}],
                                        self.chat.toolset.tools, 0)
                     - self.config.compaction.max_output_tokens)
        target = max(1, min(self.config.compaction.keep_recent_tokens, available))
        contents, selected = [], []
        text = ""
        for _, message, _ in pending:
            candidate = self.chat.context.batch([*selected, message], reason=reason if not contents else None)
            if selected and estimate_content(candidate) > target:
                contents.append(text)
                selected = [message]
                text = self.chat.context.batch(selected)
            else:
                selected.append(message)
                text = candidate
        if selected:
            contents.append(text)
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
            self.chat.turn_channels.add(self.state.pending.channel if self.state.pending is not None else "in_turn")
            through, contents = self.batch(pending, self.wake_reason())
            self.store.append_batch(self.config.scene, through, contents, turn_id=turn_id,
                                    attention_state=asdict(state))
            if self.state.pending is not None and self.state.pending.channel == "direct":
                self.chat.direct_request = True
        if scheduled:
            self.chat.turn_channels.add("schedule")
            self.store.append_schedules(self.config.scene, scheduled, turn_id=turn_id)
        if self.chat.tasks is not None and self.quiet_period(self.now()) is None:
            notices = self.chat.tasks.records.pending_notices(self.config.scene)
            if notices:
                self.chat.turn_channels.add("task")
                self.store.append_task_notices(self.config.scene, notices, turn_id=turn_id)
        if self.quiet_period(self.now()) is None:
            if self.audio_ready():
                self.chat.turn_channels.add("audio")
        events = PluginStore(self.store).pending_plugin_events(
            self.config.scene, include_events=self.quiet_period(self.now()) is None)
        if events:
            self.chat.turn_channels.add("plugin")
            self.store.append_plugin_events(self.config.scene, events, turn_id=turn_id)
        self.state = state
        return True

    async def limit_notice(self, error: LimitReached) -> None:
        """At most one host explanation per blocked window, persisted before delivery."""
        quiet = self.quiet_period(self.now())
        if quiet is not None and self.quiet_direct(self.now()) == "defer":
            return
        if self.state.limit_notice_until is not None and self.state.limit_notice_until >= error.until:
            return
        state = copy.deepcopy(self.state)
        state.limit_notice_until = error.until
        until = datetime.fromtimestamp(error.until, ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
        text = f"[宿主额度说明] {error} 本时段截至 {until}，未读消息保留。"
        parts = [self.chat.expression.simulated_message([Segment("text", {"text": text})])]
        entry = self.store.prepare_limit_notice(self.config.scene, asdict(state), text + "（尚未发送）")
        self.state = state
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                await self.chat.expression.send_prepared_expression(entry, parts, channels={"limit_notice"}, quota_notice=True)
        except Exception as failure:
            self.store.expression_error(entry, f"{type(failure).__name__}: {failure}")
            self.emit({"type": "limit_notice", "status": "failed", "error": f"{type(failure).__name__}: {failure}"})

    async def _quiet_notice(self, pending: list[tuple[int, ChatMessage, float]], until: float) -> None:
        state = self.consumed_state()
        parts, note = None, None
        expressions = []
        prefix = ("[宿主安静时段固定表达；模拟，未发送到平台]\n" if self.chat.expression.send_message is None
                  else "[宿主安静时段固定表达]\n")
        if state.quiet_notice_until != until:
            expression = self.chat.expression.simulated_message([
                Segment("text", {"text": self.settings.quiet_hours.notice_text})])
            parts = split_expression(expression, self.config.text_delivery.max_chars)
            note = prefix + report_parts(parts, [], self.chat.context.render)
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
                    content, status = await self.chat.expression.send_prepared_expression(
                        entry_seq, parts, prefix=prefix, channels={"quiet_notice"})
            except LimitReached as error:
                status, error_text = "limited", f"{type(error).__name__}: {error}"
            except TimeoutError as error:
                status, error_text = "timeout", f"{type(error).__name__}: fixed notice time limit"
                content = self.store.expression_error(entry_seq, error_text)
                expressions.append(content)
            else:
                expressions.append(content)
            # Receipt callbacks may have persisted newer attention while sending.
            state = copy.deepcopy(self.state)
            own_at = self.store.last_self_time(self.config.scene)
            if own_at is not None:
                state.contact(own_at, self.settings.focus_seconds)
            self.save_state(state)
        delivery = "none" if parts is None else "simulated" if self.chat.expression.send_message is None else "onebot"
        self.emit({"type": "notice", "status": status, "error": error_text,
                   "delivery": delivery, "expressions": expressions,
                   "quiet_until": datetime.fromtimestamp(until, ZoneInfo(self.config.timezone)).isoformat()})

    async def _turn(self, channel: str, **turn) -> None:
        state = self.consumed_state()
        contact_before = state.last_contact_at
        self.state, self.resume = state, False
        result = await self.chat.run_turn(append_new=self.append_during_turn,
                                          wait_for_messages=self.wait_for_messages,
                                          attention_state=asdict(state), **turn)
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
        if result["status"] == "limited":
            self.resume = True
        result["pending_wake"] = pending_wake
        self.emit(result)
        if result["status"] == "limited" and channel == "direct":
            await self.limit_notice(LimitReached(result["error"], result["limit_until"]))
        if self.chat.toolset.pause_after_turn:
            self.chat.toolset.pause_after_turn = False
            self.set_paused(True)
        if self.chat.toolset.restart_after_turn:
            self.chat.toolset.restart_after_turn = False
            await self.chat.toolset.host_management.finish_turn()

    def observed_since(self) -> float | None:
        return self.started_at if self.config.delivery == "simulated" else self.connected_since()

    def proactive_times(self) -> tuple[float | None, float | None]:
        """Close finished observations; return the next allowed wake and the next observation end."""
        now = self.now()
        exclude = tuple(self.settings.other_bot_ids)
        if self.observed_since() is None:
            return None, None
        observe_until = self.proactive.settle(self.config.scene, now)
        wake_at, _ = self.proactive.next_at(self.config.scene, self.config.proactive, self.config.timezone,
                                            self.settings.quiet_hours, now, exclude,
                                            observed_since=self.observed_since())
        if self.state.paused:
            return None, observe_until
        temporary = self.state.temporary_quiet
        if wake_at is not None and temporary is not None and now < temporary.until:
            wake_at = max(wake_at, temporary.until)
        return wake_at, observe_until

    async def proactive_turn(self) -> None:
        async with self.execution:
            if self.ready_for_turn is not None and not await self.ready_for_turn(False):
                return
            if await self.ready_messages(wait=False) is not None:
                return
            wake_at, _ = self.proactive_times()
            now = self.now()
            if wake_at is None or wake_at > now:
                return
            idle_since = max(self.observed_since(),
                             self.proactive.last_activity(self.config.scene, tuple(self.settings.other_bot_ids)))
            zone = ZoneInfo(self.config.timezone)
            local = datetime.fromtimestamp(now, zone)
            text = Template(PROACTIVE_PROMPT.read_text(encoding="utf-8")).substitute(
                idle=idle_text(now - idle_since), timezone=self.config.timezone,
                since=datetime.fromtimestamp(idle_since, zone).isoformat(timespec="minutes"),
                now=local.isoformat(timespec="minutes"),
            ).strip()
            pending = self.store.pending_messages(self.config.scene)
            batch = self.batch(pending, "[安静前未触发唤醒的消息]") if pending else None
            events = PluginStore(self.store).pending_plugin_events(self.config.scene)
            await self._turn("proactive", batch=batch, channels={"proactive", "plugin"} if events else {"proactive"},
                            plugin_events=events or None,
                            proactive=(text, local.date().isoformat(), idle_since))

    async def run(self) -> None:
        while True:
            if self.ready_for_turn is not None and not await self.ready_for_turn(True):
                return
            ready = await self.ready_messages()
            if ready is not None:
                async with self.execution:
                    # Merging and lock acquisition may both await beyond a disconnect.
                    if self.ready_for_turn is not None and not await self.ready_for_turn(False):
                        continue
                    # Re-read after acquisition: a lock handoff can suspend even when unlocked.
                    ready = await self.ready_messages(wait=False)
                    if ready is None:
                        continue
                    pending, notice_until, scheduled = ready
                    if notice_until is not None:
                        await self._quiet_notice(pending, notice_until)
                        continue
                    quiet = self.quiet_period(self.now()) is not None
                    notices = (self.chat.tasks.records.pending_notices(self.config.scene)
                               if self.chat.tasks is not None and not quiet else [])
                    events = PluginStore(self.store).pending_plugin_events(self.config.scene, include_events=not quiet)
                    audio = not quiet and self.audio_ready()
                    channel = ("system" if scheduled or notices or events or audio
                               else self.state.pending.channel if self.state.pending else "resume")
                    channels = {name for name, present in (
                        ("schedule", scheduled), ("task", notices), ("plugin", events), ("audio", audio), ("resume", self.resume))
                        if present}
                    if self.state.pending is not None:
                        channels.add(self.state.pending.channel)
                    reason = ("[恢复未结束的对话]" if self.resume
                              else "[此前未触发唤醒的消息]" if channel == "system" and self.state.pending is None
                              else self.wake_reason())
                    batch = self.batch(pending, reason) if pending else None
                    direct = self.state.pending is not None and self.state.pending.channel == "direct"
                    wake_received_at = (self.state.pending.first_at
                                        if not scheduled and self.state.pending is not None else None)
                    await self._turn(channel, batch=batch, scheduled=scheduled, task_notices=notices,
                                    plugin_events=events or None,
                                    direct=direct, wake_received_at=wake_received_at, channels=channels)
                continue
            if self.closing:
                return
            deadline = self.schedule_deadline(self.now())
            if self.proactive is not None:
                wake_at, observe_until = self.proactive_times()
                if wake_at is not None and wake_at <= self.now():
                    # Re-enter admission if the platform dropped while idle.
                    if self.ready_for_turn is not None and not await self.ready_for_turn(False):
                        continue
                    await self.proactive_turn()
                    continue
                for at in (wake_at, observe_until):
                    if at is not None:
                        deadline = at if deadline is None else min(deadline, at)
            if deadline is None:
                await self.changed.wait()
            else:
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=max(0, deadline - self.now()))
                except TimeoutError:
                    pass  # Recheck the actual due time and quiet interval.
