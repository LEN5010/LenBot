"""Scene expression and ordered delivery shared by chat, plugins and fixed notices."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from random import choice
from string import Template
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from .chat_context import ChatContext, PROMPTS
from .chat_tools import ReactArguments, SayArguments
from .config import LabConfig
from .delivery import Expression, part_length, report_parts, split_expression
from .expression_selection import ExpressionService
from .limits import LimitReached, check_speech
from .messages import ChatMessage, Segment, Sender, SendResult
from .model_request import ChatRequest
from .persona import Persona
from .persona_stickers import PersonaSticker
from .plugin import Content, Sent
from .plugin_delivery import prepare_parts
from .reply_effect_store import ReplyEffectStore
from .sticker_assets import CollectedSticker
from .sticker_store import StickerStore
from .store import Store, encode
from .plugin_store import PluginStore


class MessageSender(Protocol):
    def __call__(self, message: ChatMessage, *, image_bytes: bytes | None = None) -> Awaitable[SendResult]: ...


class ChatExpression:
    """Build and deliver speech without owning the scene's model loop."""

    def __init__(self, config: LabConfig, persona: Persona, store: Store, *, context: ChatContext,
                 request: ChatRequest, send_message: MessageSender | None,
                 expression_service: ExpressionService | None, notify: Callable[[], None],
                 on_reply_sample: Callable[[], None] | None, now: Callable[[], float]):
        self.config, self.persona, self.store = config, persona, store
        self.context, self.request = context, request
        self.send_message, self.expression_service = send_message, expression_service
        self.notify, self.on_reply_sample, self.now = notify, on_reply_sample, now
        # Plugin sends never interleave with a multi-part expression.
        self.outlet = asyncio.Lock()

    async def express(self, turn_id: str, arguments: SayArguments, *,
                      expression_style: str | None = None, direct: bool = False) -> ChatMessage:
        check_speech(self.store, self.config)
        quote = None
        if arguments.reply_to is not None:
            quote = self.store.find_message(self.config.scene, arguments.reply_to)
            if quote is None:
                raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if self.config.voice_mode == "direct":
            text = arguments.content
        else:
            selected = []
            if self.expression_service is not None and self.config.scene in self.expression_service.scenes:
                selected = await self.expression_service.select(
                    self.config.scene, arguments.content, turn_id=turn_id, direct=direct,
                )
            messages = self.context.voice_messages(arguments, quote=quote,
                                                   expression_style=expression_style, selected=selected)
            reply = await self.request(turn_id, "voice", messages, [],
                                       expression_ids=[item["id"] for item in selected])
            text = reply.text
        segments = []
        platform_reply = (arguments.reply_to if quote is not None and (
            self.now() - quote.time > 120 or self.store.messages_after(self.config.scene, arguments.reply_to) > 3
        ) else None)
        if platform_reply is not None:
            segments.append(Segment("reply", {"id": platform_reply}))
        if arguments.mention is not None:
            segments.append(Segment("at", {"qq": arguments.mention}))
        segments.append(Segment("text", {"text": text}))
        return self.simulated_message(segments, reply_to=platform_reply)

    def react(self, arguments: ReactArguments) -> Expression:
        if arguments.reply_to is not None and self.store.find_message(self.config.scene, arguments.reply_to) is None:
            raise ValueError(f"当前场景没有平台消息 {arguments.reply_to}")
        if arguments.emotion is not None:
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
        segments.append(Segment("image", {"summary": summary}))
        return Expression(self.simulated_message(segments, reply_to=arguments.reply_to), sticker, end_turn=arguments.end_turn)

    async def deliver_expression(self, call_id: str, expression: Expression, *,
                                 turn_id: str, channels: set[str]) -> tuple[str, str]:
        parts = ([expression.message] if expression.sticker is not None else
                 split_expression(expression.message, self.config.text_delivery.max_chars))
        entry_seq = self.store.prepare_expression(
            self.config.scene, call_id, report_parts(parts, [], self.context.render),
        )
        prefix = "模拟表达（未发送到 QQ）：" if self.send_message is None else ""
        return await self.send_prepared_expression(entry_seq, parts, prefix=prefix, turn_id=turn_id,
                                                   sticker=expression.sticker, channels=channels)

    async def send_prepared_expression(self, entry_seq: int, parts: list[ChatMessage],
                                       *, prefix: str = "", turn_id: str | None = None,
                                       sticker: PersonaSticker | CollectedSticker | None = None,
                                       channels: set[str], quota_notice: bool = False) -> tuple[str, str]:
        async with self.outlet:
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
                        check_speech(self.store, self.config)
                    except LimitReached as error:
                        content = prefix + report_parts(parts, errors, self.context.render) + "\n" + str(error)
                        self.store.expression_error(entry_seq, str(error))
                        raise
                part.time = self.now()
                part.send_status = "simulated" if self.send_message is None else "unconfirmed"
                errors.append(None)
                content = report_parts(parts, errors, self.context.render)
                message_seq = self.store.start_expression_part(entry_seq, part, prefix + content,
                                                              persona_id=self.persona.id, turn_id=turn_id,
                                                              sticker=(sticker if isinstance(sticker, CollectedSticker)
                                                                       else None if sticker is None
                                                                       else (self.persona.id, sticker)))
                self.notify()
                if self.send_message is not None:
                    result = await self.send_message(part, image_bytes=None if sticker is None else sticker.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
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
                    check_speech(self.store, self.config)
                    part.time = self.now()
                    part.send_status = "simulated" if self.send_message is None else "unconfirmed"
                    errors.append(None)
                    image = prepared[index].image
                    seq = self.store.start_outgoing(part, persona_id=self.persona.id, image=(None if image is None else
                                                    (image, prepared[index].description)))
                    self.notify()
                    if self.send_message is None:
                        continue
                    result = await self.send_message(part, image_bytes=None if image is None else image.data)
                    part.send_status, part.platform_message_id = result.status, result.platform_message_id
                    errors[-1] = result.error
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
                        (PROMPTS / "next_plugin_reply.md").read_text()).substitute(
                        plugin=plugin, time=moment,
                        report=("模拟表达（未发送到 QQ）：" if self.send_message is None else "") + report).strip())
                    self.notify()
        states = {part.send_status for part in parts[:len(errors)]}
        status = "partial" if len(states) > 1 else parts[len(errors) - 1].send_status
        return Sent(status, report, tuple(part.platform_message_id for part in parts[:len(errors)]
                                         if part.send_status == "sent"))

    def simulated_message(self, segments: list[Segment], *, reply_to: str | None = None) -> ChatMessage:
        return ChatMessage(
            id=str(uuid4()), platform="qq", scene=self.config.scene, platform_message_id=None,
            sender=Sender(self.config.bot_qq, self.persona.name, None, None), time=self.now(),
            segments=segments, reply_to=reply_to, mentions_bot=False,
            is_self=True, send_status="simulated",
        )
