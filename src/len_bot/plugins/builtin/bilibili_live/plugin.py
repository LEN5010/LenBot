"""One shared poller; only an observed new source session creates invitations."""
import asyncio
import json
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.events.models import EventType
from len_bot.media.models import MessageSegment
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.tools.results import ToolResult, ToolSource
from .client import LiveClient, LiveSample
from .config import LivePluginConfig
from .events import LiveEndedSample
from .render import render_live

logger = logging.getLogger(__name__)


class LiveStatusArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    member: str | None = Field(min_length=1, description='已配置的监测成员名称或别名；null读取全部监测对象')


class Invitation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.text.strip():
            raise ValueError('邀请正文不能为空白')
        return self


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
        context.register_tool('get_live_status', '读取配置监测对象的实际直播状态及采样时间；member=null表示全部监测对象。',
            LiveStatusArguments, self.get_status,
            purpose='查询实际直播状态', aliases=('开播状态', '谁在直播'), keywords=('直播', '开播', '下播', '房间', '状态'),
            kind='read', roles=('conversation', 'work'))
        context.register_tool('get_live_subscriptions', '读取本群真实开播订阅、通知开关、监测状态与办理入口；不修改设置，member=null读取全部已配置成员。开播通知是事件订阅，按返回的实际设置和办理权限处理，不用时间提醒替代。',
            LiveStatusArguments,self.get_subscriptions,
            purpose='核对本群开播通知订阅',aliases=('直播订阅','直播时通知','停止开播通知'),
            keywords=('订阅','通知','开播提醒','取消订阅'),kind='read',roles=('conversation','work'),deferred=True)
        context.register_handler(id='live_started', description='已订阅群的真实新直播场次邀请',
            match=lambda call: call.event.payload['plugin_id'] == self.manifest.id and call.event.payload['name'] == 'live_started',
            handler=self.on_live_started, event_types=(EventType.PLUGIN_EVENT,), sources=('plugin_event',),
            priority=10, consume=True,
            available=lambda call: self.announcement_allowed(call.scene_id, call.event.payload['data']['member']),
            validate=self.validate_announcement,
            allow_mention_all=lambda call: bool(call.scene_config and call.scene_config.mention_all))
        context.register_handler(id='live_ended', description='记录订阅成员下播，不生成额外群消息',
            match=lambda call: call.event.payload['plugin_id'] == self.manifest.id and call.event.payload['name'] == 'live_ended',
            handler=self.on_live_ended, event_types=(EventType.PLUGIN_EVENT,), sources=('plugin_event',),
            priority=10, consume=True)
        context.register_hook('before_model', id='invitation_instructions', handler=self.invitation_instructions)
        context.register_hook('before_commit', id='invitation_text', handler=self.invitation_text)

    async def invitation_instructions(self, view, call):
        if call.entry_origin and call.entry_origin.entry_id == 'live_started':
            view.instructions.append(self.config.announcement_instructions)
        return view

    async def invitation_text(self, view, call):
        if call.entry_origin and call.entry_origin.entry_id == 'live_started':
            if not view.messages or any(not message for message in view.messages):
                view.stop_reason = '开播邀请必须包含正文'
        return view

    async def on_enable(self):
        if self._poll_task is None or self._poll_task.done():
            self.samples.clear()
            self._poll_task = self.context.start_task(self._poll_loop(), name='shared_live_poller')

    async def on_disable(self):
        self._poll_task = None

    async def on_unload(self):
        await self.client.close()

    def monitored_members(self):
        names = {name for _, config in self.context.scene_configs() for name in config.live_subscriptions}
        return [member for member in self.context.members if member.name in names]

    def announcement_allowed(self, scene_id, member):
        config = self.context.scene_config(scene_id)
        return bool(self.context.scene_enabled(scene_id) and config
            and 'live_started' in config.announcements and member in config.live_subscriptions)

    async def validate_announcement(self, call):
        sample = LiveSample.model_validate(call.event.payload['data'])
        if not self.announcement_allowed(call.scene_id, sample.member):
            raise ValueError('本群没有启用该成员的开播邀请')
        self.validate_session(sample.member, sample.room_id, sample.started_at)

    async def on_live_ended(self, call):
        return None

    async def on_live_started(self, call):
        sample = LiveSample.model_validate(call.event.payload['data'])
        material = ToolResult(content=sample.model_dump_json(), evidence_kind='external',
            fetched_at=sample.sampled_at, coverage='触发本群邀请的真实直播场次',
            sources=[ToolSource(event_id=call.source_event_id, url=sample.url, title=sample.member)])
        result = await call.run_agent(instructions=(
            '当前是已订阅开播公告，只根据所给真实场次写一段邀请，正确指认主播。'
            '调用 return_result 返回正文；不要决定目标群，不写全体提及，不读取群史或创建其他工作。'), input_observations=[material],
            tool_names=(), model_role=self.config.announcement_model_role, include_identity=True, input_mode='materials',
            output_mode='result_only', output_model=Invitation,
            max_steps=self.config.announcement_max_steps, max_tool_calls=self.config.announcement_max_tool_calls,
            context_tokens=self.config.announcement_context_tokens, output_tokens=self.config.announcement_output_tokens)
        # The poller may have observed a new sample while the announcement
        # model was running; never send a stale session after that transition.
        self.validate_session(sample.member, sample.room_id, sample.started_at)
        font_path = self.context.directory.parent / 'asoul_calendar' / 'resources' / 'font.ttf'
        if not font_path.is_file():
            raise ValueError('直播卡片字体文件不存在')
        png = await asyncio.to_thread(render_live, sample, font_path=font_path,
            timezone=self.context.time_settings.timezone)
        asset_id = await call.save_image(png, '真实开播场次卡片')
        await call.submit_message([MessageSegment(type='text', text=result.text), MessageSegment(type='image', asset_id=asset_id)],
            mention_all=call.scene_config.mention_all)

    def source_status(self):
        return {'last_success_at': self._last_success_at, 'last_error_at': self._last_error_at,
                'last_error': self._last_error, 'data_scope': '配置订阅对象的公共房间采样',
                'samples': [item.model_dump() for item in self.samples.values()]}

    async def _poll_loop(self):
        while True:
            for member in self.monitored_members():
                try:
                    current = await self.client.sample(member, self.context.now)
                    previous = self.samples.get(member.name)
                    self.samples[member.name] = current
                    self._last_success_at = current.sampled_at
                    if previous is None:
                        continue
                    new_session = current.is_live and (not previous.is_live or current.started_at != previous.started_at)
                    ended = previous.is_live and not current.is_live
                    if not new_session and not ended:
                        continue
                    for scene_id, _ in self.context.scene_configs():
                        if not self.announcement_allowed(scene_id, member.name):
                            continue
                        if new_session:
                            event_id = f'live-start:{current.room_id}:{current.started_at}:{scene_id}'
                            kind = 'live_started'
                            payload = current
                        else:
                            event_id = f'live-end:{previous.room_id}:{previous.started_at}:{scene_id}'
                            kind = 'live_ended'
                            payload = LiveEndedSample(**current.model_dump(), ended_session_started_at=previous.started_at)
                        if await self.context.event_store.event_exists(event_id, scene_id):
                            continue
                        await self.context.emit_event(kind, payload, event_id=event_id, scene_id=scene_id,
                            timestamp=current.sampled_at)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error_at = self.context.now()
                    self._last_error = f'{member.name}: {type(error).__name__}: {error}'
                    logger.warning('Live source collection failed: %s', self._last_error)
            await asyncio.sleep(self.config.interval_seconds)

    def current_sample(self, member):
        active = {item.name: item for item in self.monitored_members()}
        sample = self.samples.get(member)
        if member not in active or sample is None:
            raise ValueError('该对象尚未取得监测样本')
        if sample.bilibili_uid != active[member].bilibili_uid or sample.requested_room_id != active[member].room_id:
            raise ValueError('监测对象已修改，尚未取得新对象的样本')
        if self.context.now() - sample.sampled_at >= self.config.max_age_seconds:
            raise ValueError('直播样本已过期，本次查询失败；旧快照仅供面板查看')
        return sample

    def validate_session(self, member, room_id, started_at):
        sample = self.current_sample(member)
        if not sample.is_live or sample.room_id != room_id or sample.started_at != started_at:
            raise ValueError('源已不再报告该直播场次，停止旧邀请')

    async def get_status(self, arguments: LiveStatusArguments, call_context):
        name = arguments.member
        members = self.monitored_members()
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
        scene=self.context.scene_config(call_context.scene_id)
        if scene is None:
            raise ValueError('开播订阅属于已配置群，当前场景没有群订阅设置')
        members=self.context.members
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
                'notifications_enabled':self.announcement_allowed(call_context.scene_id,member.name),
                'monitoring':monitoring})
        return ToolResult(content=json.dumps({'scene_id':call_context.scene_id,'subscriptions':items,
            'change_entry':{'type':'authenticated_dashboard','path':'场景消息 → 本群设置 → 哔哩哔哩直播监测 → live_subscriptions',
                            'chat_can_modify':False,'operations':['subscribe','unsubscribe']},
            'scope':'本群公告订阅；没有个人私聊通知订阅',
            'meaning':'订阅已保存、监测样本新鲜、实际开播事件与通知送达分别核对'},ensure_ascii=False),
            fetched_at=call_context.now,evidence_kind='retrieval',coverage='current_scene_subscription_configuration')
