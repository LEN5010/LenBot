from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from len_bot.events.models import EventType
from len_bot.media.models import MessageSegment
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext
from pydantic import BaseModel, ConfigDict
from len_bot.tools.results import ToolResult

from .client import GscoreClient
from .config import GscoreConfig
from .protocol import CoreMessageReceive, CoreMessageSegment, CoreMessageSend

logger = logging.getLogger(__name__)


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class GscoreAdapterPlugin(BasePlugin):
    """A narrow Core bridge; all visible output goes through LenBot delivery."""

    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.context = context
        self.config: GscoreConfig = context.config
        self.client = GscoreClient(self.config)
        self._receiver: asyncio.Task | None = None
        self._commands_sent = 0
        # Counters are process-local; attempt and receipt identities live in the event store.
        self._last_error: str | None = None
        self._frames_received = 0

    async def on_load(self, context: PluginContext):
        context.register_handler(id='gscore_command', description='明确 /gs 命令交给独立 GSUID Core',
            match=self._matches_command, handler=self.on_command,
            priority=15, consume=True,
            available=lambda call: bool(call.scene_config and call.scene_config.command_enabled))
        context.register_handler(id='gscore_message_send', description='接收 Core 对应群目标的原生回复',
            match=lambda call: call.event.payload['plugin_id'] == self.manifest.id and call.event.payload['name'] == 'core_message_send',
            handler=self.on_core_event, event_types=(EventType.PLUGIN_EVENT,), sources=('plugin_event',),
            priority=12, consume=True,
            available=lambda call: bool(call.scene_config and call.scene_config.allow_core_push))
        context.register_hook('after_delivery', id='gscore_recall_receipt', handler=self.after_delivery, scope='own')
        context.register_tool('gscore_status', '读取 GSUID Core 桥接连接状态；不会执行游戏命令。',
            EmptyArguments, self.status, purpose='查询 GSUID Core 状态', aliases=('游戏桥接状态',), keywords=('GSUID', '游戏', 'Core'),
            kind='read', roles=('conversation', 'work'))

    def _matches_command(self, call: PluginCallContext) -> bool:
        parts = call.event.raw_text.strip().split(maxsplit=1)
        if not parts:
            return False
        scene = call.scene_config
        prefix = scene.command_prefix if scene and scene.command_prefix else self.config.command_prefix
        return parts[0] == prefix

    async def on_enable(self):
        self._receiver = self.context.start_task(self._receive_loop(), name='gscore_receive')

    async def on_disable(self):
        if self._receiver:
            self._receiver.cancel()
            await asyncio.gather(self._receiver, return_exceptions=True)
            self._receiver = None
        await self.client.close()

    async def on_unload(self):
        await self.on_disable()

    def _command_message(self, call: PluginCallContext, command: str) -> CoreMessageReceive:
        event = call.event
        payload = event.payload
        raw_message = payload.get('message_id')
        reply_to = payload.get('reply_to_message_id')
        content: list[CoreMessageSegment] = []
        if reply_to is not None:
            content.append(CoreMessageSegment(type='reply', data={'id': str(reply_to)}))
        content.append(CoreMessageSegment(type='text', data=command))
        group_id = call.scene_id.removeprefix('group:')
        return CoreMessageReceive(bot_id=self.config.platform_bot_id, bot_self_id=self.config.bot_self_id,
            msg_id=str(raw_message) if raw_message is not None else event.id, user_type='group',
            group_id=group_id, user_id=call.requester_qq_uid, sender=payload.get('sender') or {},
            user_pm=3, content=content)

    async def on_command(self, call: PluginCallContext):
        parts = call.event.raw_text.strip().split(maxsplit=1)
        prefix, command = parts[0], parts[1] if len(parts) > 1 else ''
        scene = call.scene_config
        effective_prefix = scene.command_prefix if scene and scene.command_prefix else self.config.command_prefix
        if prefix != effective_prefix or not command.strip():
            raise ValueError(f'GSUID Core 命令格式为 {effective_prefix} <命令>')
        if scene is None or not scene.command_enabled:
            raise ValueError('本群未启用 GSUID Core 命令')
        await self.context._host.validate_call(call)
        if self.config.bot_self_id != str(self.context._runtime.config.bot_qq):
            raise ValueError('Core bot_self_id 与当前 OneBot 实际身份不一致')
        message = self._command_message(call, command.strip())
        if not await self._claim('command', call.event.id, call.scene_id, {'source_event_id': call.event.id,
                'bot_self_id': self.config.bot_self_id, 'state': 'attempted_unknown'}):
            raise ValueError('该原始消息已经尝试交给 Core；恢复和重连不重放命令')
        try:
            await self.client.send_frame(message.model_dump(exclude_none=True))
        except Exception as error:
            await self._trace('command', call.event.id, call.scene_id, state='unknown', error_code=type(error).__name__)
            raise ValueError('Core 命令发送结果未知；不自动重试') from None
        self._commands_sent += 1
        await self._trace('command', call.event.id, call.scene_id, state='forwarded',
            note='WebSocket 已提交；不表示游戏命令业务完成')
        return None

    def source_status(self):
        return {'connected': self.client._socket is not None, 'core_version': self.config.core_version,
            'commands_sent_this_process': self._commands_sent, 'frames_received_this_process': self._frames_received,
            'last_error_code': self._last_error, 'identity_matches': self.config.bot_self_id == str(self.context._runtime.config.bot_qq),
            'support_matrix': [
                {'type': '群文字 / at / 图片', 'status': 'implemented_unverified', 'detail': '需要逐帧 echo；图片登记到原媒体资产链'},
                {'type': '语音 / 视频 / 普通文件', 'status': 'unsupported', 'detail': '整帧拒绝，不扁平化或借用工作文件权限'},
                {'type': '合并转发 / 按钮 / 撤回控制', 'status': 'unsupported', 'detail': '整帧拒绝'},
                {'type': '私聊 / 频道 / 私聊登录', 'status': 'unsupported', 'detail': '仅接受配置的 QQ 群目标'},
                {'type': '无 echo 帧', 'status': 'unsupported', 'detail': '缺少逐帧稳定身份，不猜 msg_id 为帧身份'}],
            'verification': '源码支持范围；实际 Core 版本及逐帧 OneBot 回执仍须现场核对'}

    async def status(self, arguments, call_context: PluginCallContext):
        return ToolResult(status='ok', evidence_kind='retrieval', coverage='gscore_adapter_status',
            content=json.dumps(self.source_status(), ensure_ascii=False))

    async def _trace(self, kind, ref_id, scene_id, **payload):
        await self.context.event_store.save_trace(kind='gscore_' + kind, ref_id=ref_id,
            scene_id=scene_id, payload=payload)

    async def _claim(self, kind, identity, scene_id, payload):
        store = self.context.event_store
        async with store._write_lock:
            try:
                key = 'gscore:' + json.dumps([self.config.bot_self_id, kind, identity], ensure_ascii=False)
                cursor = await store._db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)',
                    (key, 'CORE_BRIDGE_ATTEMPTED', scene_id, 'plugin:gscore_adapter', store.clock(),
                     json.dumps({'kind': kind, **payload}, ensure_ascii=False), '{"conversation_excluded":true}'))
                await store._db.commit()
                return cursor.rowcount == 1
            except BaseException:
                await store._db.rollback()
                raise

    async def _receive_loop(self):
        while True:
            try:
                frame = await self.client.receive_once()
                await self._handle_core_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._last_error = type(error).__name__
                logger.warning('GSUID Core receive failed: %s', self._last_error)
                await self.client.close()
                await asyncio.sleep(self.config.reconnect_seconds)

    def _target_scene(self, message: CoreMessageSend) -> str | None:
        if message.target_type != 'group' or message.target_id is None:
            return None
        target = str(message.target_id)
        if target.startswith('group:'):
            target = target.removeprefix('group:')
        if not target.isdigit() or int(target) <= 0:
            return None
        return f'group:{target}'

    async def _handle_core_frame(self, frame: dict):
        # Core log packets have no target/content and are not platform messages.
        if not frame.get('target_type') and not frame.get('content'):
            return
        message = CoreMessageSend.model_validate(frame)
        if message.bot_id == self.config.core_bot_id:
            return
        if message.bot_id != self.config.platform_bot_id:
            logger.warning('Ignoring Core frame for unconfigured platform identity')
            return
        if message.bot_self_id != self.config.bot_self_id or self.config.bot_self_id != str(self.context._runtime.config.bot_qq):
            logger.warning('Ignoring Core frame for unconfigured OneBot identity')
            return
        scene_id = self._target_scene(message)
        if scene_id is None:
            logger.warning('Ignoring unsupported Core target %s/%s', message.target_type, message.target_id)
            await self._send_receipt(message.echo, None)
            return
        scene = self.context.scene_config(scene_id)
        if scene is None or not scene.allow_core_push:
            logger.warning('Ignoring Core push for disabled scene %s', scene_id)
            await self._send_receipt(message.echo, None)
            return
        if not message.echo:
            await self._trace('frame', message.msg_id, scene_id, state='unsupported', reason='frame_echo_missing')
            return
        event_id = 'gscore:send:' + json.dumps([self.config.bot_self_id, message.echo], ensure_ascii=False)
        if not await self._claim('frame', message.echo, scene_id,
                {'source_event_id': event_id, 'echo': message.echo, 'state': 'received'}):
            return
        self._frames_received += 1
        await self.context.emit_event('core_message_send', message, scene_id=scene_id,
            event_id=event_id, timestamp=self.context.now())

    async def _segments(self, message: CoreMessageSend, call: PluginCallContext) -> tuple[list[MessageSegment], list[str]]:
        segments: list[MessageSegment] = []
        unsupported: list[str] = []
        for item in message.content or []:
            data: Any = item.data
            if item.type == 'text' and isinstance(data, str) and data:
                segments.append(MessageSegment(type='text', text=data))
            elif item.type == 'at':
                value = data.get('qq') if isinstance(data, dict) else data
                if str(value).isdigit() and int(value) > 0:
                    segments.append(MessageSegment(type='at', qq_uid=str(value)))
                else:
                    unsupported.append('at')
            elif item.type == 'image':
                encoded = data.get('base64') if isinstance(data, dict) else data
                if isinstance(encoded, str) and encoded.startswith('base64://'):
                    encoded = encoded.removeprefix('base64://')
                if isinstance(encoded, str) and encoded.startswith(('https://', 'http://')):
                    observed = await call.invoke_tool('read_web_media', {'url': encoded, 'page': None})
                    if observed.status in {'ok', 'partial'} and observed.attachments:
                        segments.append(MessageSegment(type='image', asset_id=observed.attachments[0]))
                    else:
                        unsupported.append('image_url')
                    continue
                if not isinstance(encoded, str) or len(encoded) > 16_000_000:
                    unsupported.append('image')
                    continue
                try:
                    image = base64.b64decode(encoded, validate=True)
                    asset_id = await call.save_image(image, 'GSUID Core 原生图片')
                    segments.append(MessageSegment(type='image', asset_id=asset_id))
                except (ValueError, OSError):
                    unsupported.append('image')
            else:
                unsupported.append(item.type)
        return segments, unsupported

    async def on_core_event(self, call: PluginCallContext):
        message = CoreMessageSend.model_validate(call.event.payload['data'])
        segments, unsupported = await self._segments(message, call)
        if unsupported:
            logger.warning('Core frame contains unsupported message segments: %s', sorted(set(unsupported)))
            await self._trace('frame', call.event.id, call.scene_id, state='unsupported', types=sorted(set(unsupported)))
            await self._send_receipt(message.echo, None)
            return
        if not segments:
            await self._send_receipt(message.echo, None)
            return
        try:
            await call.submit_message(segments)
        except Exception:
            await self._send_receipt(message.echo, None)
            raise

    async def after_delivery(self, view, call: PluginCallContext):
        payload = view.receipt.get('payload') or {}
        source_event_id = payload.get('origin_event_id')
        if not source_event_id:
            return view
        events = await self.context.event_store.events_by_ids(call.scene_id, [source_event_id], call.cutoff_rowid)
        if not events:
            return view
        source = events[0]
        if source.payload.get('plugin_id') != self.manifest.id or source.payload.get('name') != 'core_message_send':
            return view
        message = CoreMessageSend.model_validate(source.payload['data'])
        status = payload.get('delivery_status')
        message_id = payload.get('message_id') if status == 'sent' else None
        await self._trace('delivery', source_event_id, call.scene_id, status=status, message_id=message_id,
            action_id=payload.get('action_id'), receipt_event_id=view.receipt.get('id'))
        await self._send_receipt(message.echo, message_id)
        return view

    async def _send_receipt(self, echo: str | None, message_id: str | None):
        if not echo:
            return
        if not await self._claim('receipt', echo, 'system:gscore', {'echo': echo, 'message_id': message_id, 'state': 'attempted_unknown'}):
            return
        receipt = {'bot_id': self.config.platform_bot_id, 'bot_self_id': self.config.bot_self_id,
            'user_id': '', 'content': [{'type': 'recall_message_id', 'data': {'echo': echo, 'id': message_id}}]}
        try:
            await self.client.send_frame(receipt)
            await self._trace('receipt', echo, 'system:gscore', state='forwarded', message_id=message_id)
        except Exception as error:
            await self._trace('receipt', echo, 'system:gscore', state='unknown', error_code=type(error).__name__)
            logger.warning('Could not send Core recall_message_id receipt: %s', type(error).__name__)
