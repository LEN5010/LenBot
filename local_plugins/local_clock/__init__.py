"""Current business time as a read tool, a command and an optional Agent reply."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from len_bot.plugins.api import BasePlugin, EmptySceneConfig, ExactText, MessageSegment, PluginCallContext, PluginContext, PluginPermission, PluginSpec, PluginType, ToolResult, ToolSource


class ClockConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    tool_timeout_seconds: float = Field(gt=0,title='读取超时（秒）')
    model_role: Literal['conversation','work'] = Field(title='简报使用的既有模型路由')
    max_steps: int = Field(ge=1,title='简报模型调用上限')
    max_tool_calls: int = Field(ge=0,title='简报工具调用上限')
    context_tokens: int = Field(gt=0,title='简报上下文容量')
    output_tokens: int = Field(gt=0,title='简报输出预留')

    @model_validator(mode='after')
    def capacity(self):
        if self.output_tokens>=self.context_tokens:
            raise ValueError('输出预留必须小于上下文容量')
        return self


class ClockSceneConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    commands: list[Literal['time_now','time_brief']] = Field(title='开放的命令',
        description='time_now 对应“现在几点”，time_brief 对应“时间简报”；空列表关闭命令，读取工具仍受本群插件开关控制')


class ClockReading(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    local_time: AwareDatetime
    timezone: str
    weekday: str


class LocalClock(BasePlugin):
    def __init__(self,context: PluginContext):
        super().__init__(context.manifest)
        self.context=context
        self.config: ClockConfig=context.config
        self.zone=ZoneInfo(context.time_settings.timezone)

    async def on_load(self,context: PluginContext):
        context.register_tool('local_time_now','读取 LenBot 的当前时钟，按已配置的业务时区返回日期、星期和时间。',
            EmptySceneConfig,self.read_time,purpose='读取当前业务时间',aliases=('当前时间','现在几点'),
            keywords=('时间','日期','星期','时区'),kind='read',roles=('conversation','work'))
        context.register_handler(id='time_now',description='现在几点：直接读取并回复业务时间',
            match=ExactText(('现在几点',)),handler=self.on_now,priority=30,consume=True,
            available=lambda call:'time_now' in call.scene_config.commands)
        context.register_handler(id='time_brief',description='时间简报：由插件 Agent 组织时间说明',
            match=ExactText(('时间简报',)),handler=self.on_brief,priority=30,consume=True,
            available=lambda call:'time_brief' in call.scene_config.commands)

    async def read_time(self,arguments: EmptySceneConfig,call: PluginCallContext) -> ToolResult:
        now=self.context.now()
        current=datetime.fromtimestamp(now,self.zone)
        reading=ClockReading(local_time=current,timezone=self.zone.key,weekday='星期'+'一二三四五六日'[current.weekday()])
        return ToolResult(status='ok',content=reading.model_dump_json(),fetched_at=now,
            coverage='当前运行时时钟的一次读取，使用已配置业务时区',evidence_kind='retrieval',
            sources=[ToolSource(title='LenBot 运行时时钟')])

    async def on_now(self,call: PluginCallContext):
        observed=await call.invoke_tool('local_time_now',EmptySceneConfig())
        if observed.status!='ok':raise ValueError(observed.content)
        reading=ClockReading.model_validate_json(observed.content)
        text=f'{reading.local_time:%Y年%m月%d日} {reading.weekday} {reading.local_time:%H:%M:%S}（{reading.timezone}）'
        await call.submit_message([MessageSegment(type='text',text=text)])

    async def on_brief(self,call: PluginCallContext):
        observed=await call.invoke_tool('local_time_now',EmptySceneConfig())
        if observed.status!='ok':raise ValueError(observed.content)
        await call.run_agent(instructions='根据本次真实时钟读取，向命令发起者简短说明日期、星期、时间和时区。'
            '通过respond提交一条文字，sources记录该命令为replied，next=end。',
            input_observations=[observed],tool_names=(),input_mode='source',include_identity=False,
            output_mode='respond',model_role=self.config.model_role,max_steps=self.config.max_steps,
            max_tool_calls=self.config.max_tool_calls,context_tokens=self.config.context_tokens,
            output_tokens=self.config.output_tokens)


def validate(config,root):
    if root.plugins['local_clock'].enabled and root.time is None:
        raise ValueError('启用业务时钟前需要在根配置中设置 time')


PLUGIN=PluginSpec(id='local_clock',name='业务时钟',version='1.0.0',
    description='读取当前业务时间；精确命令可直接回复，时间简报由插件选择既有 Agent 表达。',
    config_model=ClockConfig,scene_config_model=ClockSceneConfig,create=LocalClock,
    validate_config=validate,call_timeout=lambda config:config.tool_timeout_seconds,
    permissions=(PluginPermission.REGISTER_TOOL,),plugin_type=PluginType.HYBRID)
