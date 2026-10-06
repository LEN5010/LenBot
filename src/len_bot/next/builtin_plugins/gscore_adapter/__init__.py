"""Explicit /gs commands and one owned Core connection; all delivery uses the host."""
import asyncio
import base64
import binascii
import json
import math
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.protocol import State

from len_bot.next.image_assets import MAX_IMAGE_BYTES
from len_bot.next.plugin import Image, Invocation, Mention, Plugin, PluginContext, Text, command, tool
from len_bot.next.tools.http_read import fetch_public

from .protocol import AtPart, Frame, ImagePart, ImageSize, TextPart, parse_frame


class Gscore(Plugin):
    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        url = urlsplit(ctx.config['ws_url'])
        if (url.scheme not in {'ws','wss'} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError('ws_url须为没有凭据/query/fragment的完整ws/wss地址')
        if (not math.isfinite(ctx.config['timeout_seconds']) or ctx.config['timeout_seconds'] <= 0
                or ctx.config['max_frame_bytes'] <= 0):
            raise ValueError('timeout_seconds须为正有限数，max_frame_bytes须大于0')
        if any(c in ctx.config['access_token'] for c in '\r\n'):
            raise ValueError('access_token不能含换行')
        self.socket = None
        self.reader = None
        self.connection_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.commands_submitted = 0
        self.frames_received = 0
        self.last_error = None

    @property
    def connected(self):
        return (self.socket is not None and self.socket.state is State.OPEN
                and self.reader is not None and not self.reader.done())

    def safe_error(self, error: Exception) -> RuntimeError:
        text = f'{type(error).__name__}: {error}'
        token = self.ctx.config['access_token']
        if token:
            text = text.replace(token, '[redacted]').replace(urlencode({'token':token})[6:], '[redacted]')
        return RuntimeError(text)

    async def start(self):
        await self.open_connection()

    async def stop(self):
        async with self.connection_lock:
            await self.disconnect()

    async def disconnect(self):
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        if self.socket is not None:
            await self.socket.close()
            self.socket = None

    async def open_connection(self):
        async with self.connection_lock:
            if self.connected:
                return
            await self.disconnect()
            config = self.ctx.config
            url = urlsplit(config['ws_url'])
            token = config['access_token']
            query = urlencode({config['token_query_parameter']:token}) if token and config['token_query_parameter'] else ''
            endpoint = urlunsplit((url.scheme,url.netloc,url.path,query,''))
            try:
                self.socket = await connect(endpoint, additional_headers={'Authorization':f'Bearer {token}'} if token else None,
                    open_timeout=config['timeout_seconds'], close_timeout=config['timeout_seconds'],
                    max_size=config['max_frame_bytes'], max_queue=8, proxy=None)
            except Exception as error:
                raise self.safe_error(error) from error
            self.last_error = None
            self.reader = self.ctx.start_task('Core接收连接', self.receive(self.socket))

    async def send_core(self, body):
        async with self.send_lock:
            if not self.connected:
                raise ConnectionError('Core连接未就绪；未重连或重发，可显式使用/gs连接')
            try:
                await self.socket.send(json.dumps(body, ensure_ascii=False))
            except Exception as error:
                raise self.safe_error(error) from error

    @command('gs', '把明确的/gs <命令>交给配置的GSUID Core，不转发普通群聊')
    async def execute(self, ctx: Invocation, args: str):
        if not args.strip():
            raise ValueError('用法：/gs <Core命令>')
        message = ctx.message
        unsupported = [part.type for part in message.segments if part.type not in {'text','mention','reply'}]
        if unsupported:
            raise ValueError(f'/gs目前只提交文字、@和引用ID；未提交的消息段：{unsupported}')
        platform, kind, target = ctx.scene.split(':',2)
        content = [{'type':'text','data':args}]
        content.extend({'type':'at','data':'all' if part.data['user'] == 'all' else str(part.data['user']).split(':',1)[1]} for part in message.segments if part.type=='mention')
        # A real quote remains a quote ID, never an invented source message.
        if message.reply_to is not None:
            content.append({'type':'reply_id','data':message.reply_to})
        await self.send_core({'bot_id':'onebot','bot_self_id':self.ctx.bot_id.split(':',1)[1],
            'msg_id':message.platform_message_id,'user_type':'group' if kind=='group' else 'direct',
            'group_id':target if kind=='group' else None,'user_id':message.sender.uid.split(':',1)[1],
            'sender':{'user_id':message.sender.uid.split(':',1)[1],'nickname':message.sender.nickname,
                      'card':message.sender.card,'role':message.sender.role},'user_pm':6,'content':content})
        self.commands_submitted += 1

    @command('gs连接', '明确连接一次固定Core端点；不会重放之前命令')
    async def reconnect(self, ctx: Invocation, args: str):
        if args:
            raise ValueError('/gs连接不接受参数')
        await self.open_connection()
        await ctx.reply('Core连接已就绪；没有重放之前的命令。')

    @tool('gscore_status', '只读GSUID Core连接、实际已提交命令数和最近错误，不执行游戏命令')
    async def status(self, ctx: Invocation) -> str:
        return json.dumps({'connected':self.connected,'commands_submitted':self.commands_submitted,
            'frames_received':self.frames_received,'last_error':self.last_error,
            'note':'提交到WebSocket不代表Core已执行完成；没有自动重连或重发。'}, ensure_ascii=False)

    async def image_bytes(self, value: str) -> bytes:
        if not value.startswith('link://'):
            # Core's current URL-to-base64 path returns bare base64, while byte images carry the prefix.
            encoded = value.removeprefix('base64://')
            if len(encoded)>4*((MAX_IMAGE_BYTES+2)//3):
                raise ValueError('Core图片超过宿主原件字节上限')
            try:
                return base64.b64decode(encoded,validate=True)
            except binascii.Error as error:
                raise ValueError(f'Core图片base64不合法：{error}；原文片段={value[:120]!r}') from error
        url = value[7:]
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http','https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError('Core图片link必须为不含凭据/fragment的HTTP(S)地址')
        timeout = self.ctx.config['timeout_seconds']
        async with asyncio.timeout(timeout):
            _, _, data = await fetch_public(url, timeout, lambda _type, _prefix: MAX_IMAGE_BYTES,
                                           fake_ip_networks=self.ctx.host.config.network.networks())
            return data

    async def deliver(self, frame: Frame):
        ids = None
        try:
            if 'onebot:' + frame.bot_self_id != self.ctx.bot_id:
                raise PermissionError(f'Core下行bot_self_id {frame.bot_self_id}不是本宿主身份')
            if frame.scene not in self.ctx.scenes:
                raise PermissionError(f'Core目标 {frame.scene} 未启用此插件')
            parts = []
            for part in frame.content:
                if isinstance(part,TextPart):parts.append(Text(part.data))
                elif isinstance(part,AtPart):parts.append(Mention('all' if part.data == 'all' else 'onebot:' + part.data))
                elif isinstance(part,ImagePart):parts.append(Image(await self.image_bytes(part.data),'GSUID Core返回的原图（未作视觉识别）'))
                elif isinstance(part,ImageSize):continue  # Advisory size only; actual bytes are inspected by the host.
            result = await self.ctx.send_parts(frame.scene,parts)
            ids = list(result.message_ids) or None
            if result.status != 'sent':
                self.last_error = self.ctx.report_error('Core发送结果',RuntimeError(result.report))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = self.ctx.report_error('Core下行消息',self.safe_error(error))
        if frame.echo is not None:
            await self.send_core({'bot_id':'onebot','bot_self_id':self.ctx.bot_id.split(':',1)[1],'user_id':'',
                'content':[{'type':'recall_message_id','data':{'echo':frame.echo,'id':ids}}]})

    async def receive(self, socket):
        try:
            async for raw in socket:
                self.frames_received += 1
                try:
                    frame = parse_frame(raw)
                except ValueError as error:
                    self.last_error = self.ctx.report_error('Core协议',self.safe_error(error))
                    continue
                await self.deliver(frame)
            raise ConnectionError('Core连接已关闭；未自动重连或重发')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = str(self.safe_error(error))
            raise self.safe_error(error) from error
