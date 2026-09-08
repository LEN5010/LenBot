"""One shared poller; only an observed new source session creates invitations."""
import asyncio
import json
import logging

from len_bot.events.models import Event, EventType
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginManifest, PluginPermission, PluginType
from len_bot.tools.results import ToolResult, ToolSource
from .client import LiveClient, LiveSample
from .config import LivePluginConfig

logger = logging.getLogger(__name__)


class BilibiliLiveSensor(BasePlugin):
    def __init__(self, *, config: LivePluginConfig, enabled: bool):
        super().__init__(PluginManifest(id='bilibili_live_sensor', name='哔哩哔哩直播监测',
            description='采集真实房间状态，并为订阅群的新场次生成一次邀请。',
            plugin_type=PluginType.HYBRID, enabled=enabled, config=config.model_dump(),
            config_schema=LivePluginConfig.model_json_schema(), timeout_seconds=config.tool_timeout_seconds,
            permissions=[PluginPermission.EMIT_EVENT, PluginPermission.REGISTER_TOOL],
            emitted_events=['LIVE_STARTED', 'LIVE_ENDED'], registered_tools=['get_live_status']))
        self.config = config
        self.client = LiveClient(config)
        self._poll_task = None
        self.samples: dict[str, LiveSample] = {}
        self._last_success_at = None
        self._last_error_at = None
        self._last_error = ''

    async def on_load(self, context: PluginContext):
        self.context = context
        self.runtime = context._runtime
        context.register_tool('get_live_status', '读取配置监测对象的实际直播状态及采样时间；member=null表示全部监测对象。',
            {'type': 'object', 'properties': {'member': {'type': ['string', 'null']}},
             'required': ['member'], 'additionalProperties': False}, self.get_status,
            kind='read', roles=('conversation', 'work'))
        if self.manifest.enabled:
            await self.on_enable()

    async def on_enable(self):
        if self._poll_task is None or self._poll_task.done():
            self.samples.clear()
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def on_disable(self):
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None

    async def on_unload(self):
        await self.on_disable()
        await self.client.close()

    def source_status(self):
        return {'last_success_at': self._last_success_at, 'last_error_at': self._last_error_at,
                'last_error': self._last_error, 'data_scope': '配置订阅对象的公共房间采样',
                'samples': [item.model_dump() for item in self.samples.values()]}

    async def _poll_loop(self):
        while True:
            for member in self.runtime.scene_policy.monitored_members():
                try:
                    current = await self.client.sample(member, self.runtime.clock)
                    previous = self.samples.get(member.name)
                    self.samples[member.name] = current
                    self._last_success_at = current.sampled_at
                    if previous is None:
                        continue
                    new_session = current.is_live and (not previous.is_live or current.started_at != previous.started_at)
                    ended = previous.is_live and not current.is_live
                    if not new_session and not ended:
                        continue
                    for scene_id in self.runtime.config_store.current.scenes:
                        if not self.runtime.scene_policy.announcement_allowed(scene_id, member.name):
                            continue
                        if new_session:
                            event_id = f'live-start:{current.room_id}:{current.started_at}:{scene_id}'
                            kind = EventType.LIVE_STARTED
                            payload = {**current.model_dump(), 'notification': True,
                                'raw_text': f'{current.member} 开播：{current.title}'}
                        else:
                            event_id = f'live-end:{previous.room_id}:{previous.started_at}:{scene_id}'
                            kind = EventType.LIVE_ENDED
                            payload = {**current.model_dump(), 'ended_session_started_at': previous.started_at,
                                'raw_text': f'{current.member} 已下播'}
                        if await self.context.event_store.event_exists(event_id, scene_id):
                            continue
                        await self.context.emit_event(Event(id=event_id, event_type=kind, scene_id=scene_id,
                            actor_id='plugin:bilibili_live_sensor', timestamp=current.sampled_at, payload=payload))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error_at = self.runtime.clock()
                    self._last_error = f'{member.name}: {type(error).__name__}: {error}'
                    logger.warning('Live source collection failed: %s', self._last_error)
            await asyncio.sleep(self.config.interval_seconds)

    def current_sample(self, member):
        active = {item.name: item for item in self.runtime.scene_policy.monitored_members()}
        sample = self.samples.get(member)
        if member not in active or sample is None:
            raise ValueError('该对象尚未取得监测样本')
        if sample.bilibili_uid != active[member].bilibili_uid or sample.requested_room_id != active[member].room_id:
            raise ValueError('监测对象已修改，尚未取得新对象的样本')
        if self.runtime.clock() - sample.sampled_at >= self.config.max_age_seconds:
            raise ValueError('直播样本已过期，本次查询失败；旧快照仅供面板查看')
        return sample

    def validate_session(self, member, room_id, started_at):
        sample = self.current_sample(member)
        if not sample.is_live or sample.room_id != room_id or sample.started_at != started_at:
            raise ValueError('源已不再报告该直播场次，停止旧邀请')

    async def get_status(self, arguments, call_context):
        name = arguments['member']
        members = self.runtime.scene_policy.monitored_members()
        if name is not None:
            members = [member for member in members if name == member.name or name in member.aliases]
            if not members:
                raise ValueError('指定成员不是已配置的监测对象')
        samples = [self.current_sample(member.name) for member in members]
        return ToolResult(status='ok' if samples else 'no_results',
            content=json.dumps({'samples': [item.model_dump() for item in samples]}, ensure_ascii=False),
            fetched_at=max((item.sampled_at for item in samples), default=call_context.now),
            cached=True, evidence_kind='external', coverage='配置监测对象的实际房间状态；不等同于日程安排',
            sources=[ToolSource(url=item.url, title=item.member) for item in samples])
