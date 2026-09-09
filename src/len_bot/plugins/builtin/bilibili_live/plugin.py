"""One shared poller; only an observed new source session creates invitations."""
import asyncio
import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from len_bot.events.models import Event, EventType
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.tools.results import ToolResult, ToolSource
from .client import LiveClient, LiveSample
from .config import LivePluginConfig

logger = logging.getLogger(__name__)


class LiveStatusArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    member: str | None = Field(min_length=1, description='已配置的监测成员名称或别名；null读取全部监测对象')


class BilibiliLiveSensor(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: LivePluginConfig = context.config
        self.client = LiveClient(self.config)
        self._poll_task = None
        self.samples: dict[str, LiveSample] = {}
        self._last_success_at = None
        self._last_error_at = None
        self._last_error = ''

    async def on_load(self, context: PluginContext):
        self.context = context
        self.runtime = context._runtime
        context.register_tool('get_live_status', '读取配置监测对象的实际直播状态及采样时间；member=null表示全部监测对象。',
            LiveStatusArguments, self.get_status,
            purpose='查询实际直播状态', aliases=('开播状态', '谁在直播'), keywords=('直播', '开播', '下播', '房间', '状态'),
            kind='read', roles=('conversation', 'work'))
        context.register_tool('get_live_subscriptions', '读取本群真实开播订阅、通知开关、监测状态与办理入口；不修改设置，member=null读取全部已配置成员。',
            LiveStatusArguments,self.get_subscriptions,
            purpose='核对本群开播通知订阅',aliases=('直播订阅','直播时通知','停止开播通知'),
            keywords=('订阅','通知','开播提醒','取消订阅'),kind='read',roles=('conversation','work'),deferred=True)

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

    async def get_status(self, arguments: LiveStatusArguments, call_context):
        name = arguments.member
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

    async def get_subscriptions(self, arguments: LiveStatusArguments, call_context):
        scene=self.runtime.scene_policy.scene(call_context.scene_id)
        if scene is None:
            raise ValueError('开播订阅属于已配置群，当前场景没有群订阅设置')
        members=self.runtime.config_store.current.members
        if arguments.member is not None:
            members=[member for member in members if arguments.member==member.name or arguments.member in member.aliases]
            if not members:raise ValueError('没有对应的已配置成员，请使用成员名称或已登记别名')
        items=[]
        for member in members:
            subscribed=member.name in scene.live_subscriptions
            if subscribed:
                try:
                    sample=self.current_sample(member.name)
                    monitoring={'status':'fresh','sampled_at':sample.sampled_at,'is_live':sample.is_live}
                except ValueError as error:
                    monitoring={'status':'unavailable','reason':str(error)}
            else:
                monitoring={'status':'not_subscribed_in_scene'}
            items.append({'member':member.name,'subscribed':subscribed,
                'notifications_enabled':self.runtime.scene_policy.announcement_allowed(call_context.scene_id,member.name),
                'monitoring':monitoring})
        return ToolResult(content=json.dumps({'scene_id':call_context.scene_id,'subscriptions':items,
            'change_entry':{'type':'authenticated_dashboard','path':'场景消息 → 本群设置 → 订阅主播',
                            'chat_can_modify':False,'operations':['subscribe','unsubscribe']},
            'scope':'本群公告订阅；没有个人私聊通知订阅',
            'meaning':'订阅已保存、监测样本新鲜、实际开播事件与通知送达分别核对'},ensure_ascii=False),
            fetched_at=call_context.now,evidence_kind='retrieval',coverage='current_scene_subscription_configuration')
