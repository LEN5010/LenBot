"""Scene expression and ordered delivery shared by chat, plugins and fixed notices."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from random import choice
from string import Template
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from .context import ChatContext
from .tools import ReactArguments, SayArguments
from ..config import LabConfig
from ..platform.delivery import Expression, part_length, report_parts, split_expression
from ..models.limits import LimitReached, check_speech
from ..platform.messages import ChatMessage, Segment, Sender, SendResult
from ..persona.profile import Persona
from ..persona.stickers import PersonaSticker
from ...plugin import Content, Sent
from ..plugins.delivery import prepare_parts
from ..learning.reply_effect_store import ReplyEffectStore
from ..learning.sticker_assets import CollectedSticker
from ..learning.sticker_store import StickerStore
from ..storage.store import Store, encode
from ..plugins.store import PluginStore
from ..runtime.logs import log_event
from ..prompt_files import read_prompt

logger = logging.getLogger(__name__)


def log_sent(seq: int, part: ChatMessage, error: str | None, *, index: int, parts: int, plugin: str | None = None) -> None:
    """One outbound part: stored seq, platform result and ID; the turn or plugin comes from the log context."""
    log_event(logger, 'message_sent', level=logging.INFO if part.send_status in {'sent', 'simulated'} else logging.WARNING,
              message_seq=seq, send_status=part.send_status, platform_message_id=part.platform_message_id,
              part=index + 1, parts=parts, error=error, **({} if plugin is None else {'plugin': plugin}))


def simulate(part: ChatMessage, simulated: bool) -> None:
    """Mark a part about to be sent. A simulated part gets a local message ID, so a later message can quote it
    the way it quotes a delivered one; a real part waits for the platform's ID."""
    if simulated:
        part.send_status, part.platform_message_id = "simulated", f"simulated:{uuid4()}"
    else:
        part.send_status = "unconfirmed"


class MessageSender(Protocol):
    def __call__(self, message: ChatMessage, *, image_bytes: bytes | None = None) -> Awaitable[SendResult]: ...


class ChatExpression:
    """Build and deliver speech without owning the scene's model loop."""

    def __init__(self, config: LabConfig, persona: Persona, store: Store, *, context: ChatContext,
                 send_message: MessageSender | None, notify: Callable[[], None],
                 on_reply_sample: Callable[[], None] | None, now: Callable[[], float],
                 exclude_from_memory: Callable[[int], None] | None = None):
        self.config, self.persona, self.store = config, persona, store
        # Plugin output (pushes, cards, summaries) is not something that happened in the group.
        self.exclude_from_memory = exclude_from_memory
        self.context = context
        self.send_message = send_message
        self.notify, self.on_reply_sample, self.now = notify, on_reply_sample, now
        # Plugin sends never interleave with a multi-part expression.
        self.outlet = asyncio.Lock()

    def check_send_available(self) -> None:
        until = self.store.bot_muted_until(self.config.scene, self.config.bot_id)
        if until is not None:
            raise ValueError(f"平台 group_ban 通知：当前 Bot 禁言至 {until}，未发送")

    async def express(self, turn_id: str, arguments: SayArguments, *,
                      expression_style: str | None = None, direct: bool = False) -> Expression:
        self.check_send_available()
        check_speech(self.store, self.config)
        quote = None
        if arguments.reply_to is not None:
            quote = self.store.find_message(self.config.scene, arguments.reply_to)
            if quote is None:
                raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        text = arguments.content
        segments = []
        platform_reply = (arguments.reply_to if quote is not None and (
            self.now() - quote.time > 120 or self.store.messages_after(self.config.scene, arguments.reply_to) > 3
        ) else None)
        if platform_reply is not None:
            segments.append(Segment("reply", {"id": platform_reply}))
        if arguments.mention is not None:
            if arguments.mention.partition(':')[0] != self.config.scene.partition(':')[0]:
                raise ValueError(f"提及账号与当前场景平台不同：{arguments.mention!r}")
            segments.append(Segment("mention", {"user": arguments.mention}))
        segments.append(Segment("text", {"text": text}))
        sticker = None
        if arguments.sticker is not None:
            selected = self.react(ReactArguments(**arguments.sticker.model_dump()))
            segments.extend(selected.message.segments)
            sticker = selected.sticker
        return Expression(self.simulated_message(segments, reply_to=platform_reply), sticker,
                          end_turn=arguments.end_turn)

    def react(self, arguments: ReactArguments) -> Expression:
        self.check_send_available()
        if arguments.reply_to is not None and self.store.find_message(self.config.scene, arguments.reply_to) is None:
            raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if arguments.file is not None:
            sticker = self.persona.stickers.get(arguments.file)
            if sticker is None:
                raise ValueError(f"当前角色没有表情文件：{arguments.file}")
            matches = [sticker]
        elif arguments.emotion is not None:
            matches = [sticker for sticker in self.persona.stickers.values()
                       if arguments.emotion.casefold() in {value.casefold() for value in sticker.emotions}]
        else:
            query = arguments.query.casefold()
            matches = [sticker for sticker in self.persona.stickers.values()
                       if any(query in value.casefold() for value in
                              (sticker.description, *sticker.emotions, *sticker.tags))]
        if matches:
            usage = self.store.sticker_usage(self.config.scene, self.persona.id)
            least = min(usage.get(sticker.file, 0) for sticker in matches)
            sticker = choice([sticker for sticker in matches if usage.get(sticker.file, 0) == least])
            summary = f"角色表情 {sticker.file}：{sticker.description}"
        elif self.config.learning is not None and self.config.learning.collect_stickers:
            records = StickerStore(self.store)
            collected = records.matches(self.config.scene, emotion=arguments.emotion, query=arguments.query)
            if not collected:
                raise ValueError(f"角色及本群已采用表情无匹配：{encode(arguments.model_dump(exclude_none=True))}")
            least = min(item["uses"] for item in collected)
            selected = choice([item for item in collected if item["uses"] == least])
            sticker = records.asset(self.config.scene, selected["id"])
            source = self.store.read_message(self.config.scene, sticker.source_message_seq)
            summary = (f"本群表情，来源平台消息 {source.platform_message_id} 第{sticker.source_image_index}张："
                       f"{sticker.description}")
        else:
            raise ValueError(f"角色表情无匹配：{encode(arguments.model_dump(exclude_none=True))}")
        segments = []
        if arguments.reply_to is not None:
            segments.append(Segment("reply", {"id": arguments.reply_to}))
        segments.append(Segment("image", {"summary": summary, "sub_type": 1}))
        return Expression(self.simulated_message(segments, reply_to=arguments.reply_to), sticker, end_turn=arguments.end_turn)

    async def deliver_expression(self, call_id: str, expression: Expression, *,
                                 turn_id: str, channels: set[str]) -> tuple[str, str]:
        parts = ([expression.message] if expression.sticker is not None else
                 split_expression(expression.message, self.config.text_delivery.max_chars))
        entry_seq = self.store.prepare_expression(
            self.config.scene, call_id, report_parts(parts, [], self.context.render),
        )
        prefix = "模拟表达（未发送到平台）：" if self.send_message is None else ""
        return await self.send_prepared_expression(entry_seq, parts, prefix=prefix, turn_id=turn_id,
                                                   sticker=expression.sticker, channels=channels)

    async def send_prepared_expression(self, entry_seq: int, parts: list[ChatMessage],
                                       *, prefix: str = "", turn_id: str | None = None,
                                       sticker: PersonaSticker | CollectedSticker | None = None,
                                       channels: set[str], quota_notice: bool = False) -> tuple[str, str]:
        async with self.outlet:
            self.check_send_available()
            return await self._send_prepared(entry_seq, parts, prefix=prefix, turn_id=turn_id,
                                             sticker=sticker, channels=channels, quota_notice=quota_notice)

    async def _send_prepared(self, entry_seq: int, parts: list[ChatMessage], *, prefix: str,
                             turn_id: str | None, sticker: PersonaSticker | CollectedSticker | None,
                             channels: set[str], quota_notice: bool) -> tuple[str, str]:
        errors: list[str | None] = []
        settings = self.config.text_delivery
        # Snapshot now: a later append in this turn does not change why this was said.
        seen = set(channels)
        confirmed: list[tuple[int, float]] = []
        try:
            for index, part in enumerate(parts):
                if index:
                    delay = min(settings.max_interval_seconds,
                                max(settings.min_interval_seconds, part_length(part) / settings.chars_per_second))
                    await asyncio.sleep(delay)
                if not quota_notice:
                    try:
                        self.check_send_available()
                        check_speech(self.store, self.config)
                    except LimitReached as error:
                        content = prefix + report_parts(parts, errors, self.context.render) + "\n" + str(error)
                        self.store.expression_error(entry_seq, str(error))
                        raise
                part.time = self.now()
                simulate(part, self.send_message is None)
                errors.append(None)
                content = report_parts(parts, errors, self.context.render)
                message_seq = self.store.start_expression_part(entry_seq, part, prefix + content,
                                                              persona_id=self.persona.id, turn_id=turn_id,
                                                              sticker=(sticker if isinstance(sticker, CollectedSticker)
                                                                       else None if sticker is None
                                                                       else (self.persona.id, sticker)))
                self.notify()
                if self.send_message is None:
                    log_sent(message_seq, part, None, index=index, parts=len(parts))
                if self.send_message is not None:
                    result = await self.send_message(part, image_bytes=None if sticker is None else sticker.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
                    log_sent(message_seq, part, result.error, index=index, parts=len(parts))
                    content = report_parts(parts, errors, self.context.render)
                    kept = self.store.finish_expression((message_seq, entry_seq), part, prefix + content,
                                                        turn_id=turn_id)
                    if result.status == "sent":
                        confirmed.append((kept, self.now()))
                    self.notify()
                    if result.status != "sent":
                        break
        finally:
            if (confirmed and self.config.learning is not None and self.config.learning.reply_effects):
                ReplyEffectStore(self.store).record(
                    self.config.scene, entry_seq, turn_id=turn_id, channels=sorted(seen),
                    sent=confirmed, planned_parts=len(parts),
                )
                if self.on_reply_sample is not None:
                    self.on_reply_sample()
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return content, status

    async def send_plugin_content(self, plugin: str, content: Sequence[Content], *, reply_to: str | None) -> Sent:
        """Send ordered plugin content through one scene outlet and retain actual results."""
        if reply_to is not None and self.store.find_message(self.config.scene, reply_to) is None:
            raise ValueError(f"当前场景没有平台消息 {reply_to}")
        self.check_send_available()
        check_speech(self.store, self.config)
        prepared = await prepare_parts(plugin, content, self.config.text_delivery.max_chars, reply_to)
        parts = [self.simulated_message(part.segments, reply_to=reply_to if index == 0 else None)
                 for index, part in enumerate(prepared)]
        settings = self.config.text_delivery
        errors: list[str | None] = []
        interruption: str | None = None
        async with self.outlet:
            try:
                for index, part in enumerate(parts):
                    if index:
                        await asyncio.sleep(min(settings.max_interval_seconds, max(
                            settings.min_interval_seconds, part_length(part) / settings.chars_per_second)))
                    self.check_send_available()
                    check_speech(self.store, self.config)
                    part.time = self.now()
                    simulate(part, self.send_message is None)
                    errors.append(None)
                    image = prepared[index].image
                    seq = self.store.start_outgoing(part, persona_id=self.persona.id, image=(None if image is None else
                                                    (image, prepared[index].description)))
                    if self.exclude_from_memory is not None:
                        self.exclude_from_memory(seq)
                    self.notify()
                    if self.send_message is None:
                        log_sent(seq, part, None, index=index, parts=len(parts), plugin=plugin)
                        continue
                    result = await self.send_message(part, image_bytes=None if image is None else image.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
                    log_sent(seq, part, result.error, index=index, parts=len(parts), plugin=plugin)
                    self.store.finish_expression((seq, None), part, "")
                    self.notify()
                    if result.status != "sent":
                        break
            except BaseException as error:
                interruption = f"{type(error).__name__}: {error}"
                raise
            finally:
                if errors:
                    report = report_parts(parts, errors, self.context.render)
                    if interruption is not None:
                        report += "\n" + interruption
                    moment = datetime.fromtimestamp(self.now(), ZoneInfo(self.config.timezone)).isoformat(timespec="seconds")
                    PluginStore(self.store).add_plugin_event(self.config.scene, plugin, "reply", Template(
                        read_prompt("next_plugin_reply.md")).substitute(
                        plugin=plugin, time=moment,
                        report=("模拟表达（未发送到平台）：" if self.send_message is None else "") + report).strip())
                    self.notify()
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return Sent(status, report, tuple(part.platform_message_id for part in parts[:len(errors)]
                                         if part.send_status == "sent"))

    def simulated_message(self, segments: list[Segment], *, reply_to: str | None = None) -> ChatMessage:
        return ChatMessage(
            id=str(uuid4()), platform=self.config.bot_id.split(":", 1)[0], bot_id=self.config.bot_id, scene=self.config.scene, platform_message_id=None,
            sender=Sender(self.config.bot_id, self.persona.name, None, None), time=self.now(),
            segments=segments, reply_to=reply_to, mentions_bot=False,
            is_self=True, send_status="simulated",
        )
