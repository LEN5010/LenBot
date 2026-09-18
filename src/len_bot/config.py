"""Runtime parameters parsed from the project root configuration."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AddressName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]

# These limits are captured by a new conversation or work execution segment.
EXECUTION_BUDGET_FIELDS = frozenset({
    'conversation_max_steps', 'conversation_max_tool_calls', 'conversation_window_seconds',
    'job_max_steps', 'job_max_tool_calls', 'job_max_seconds', 'maintenance_max_tool_calls',
})

from len_bot.media.file_config import FileDeliveryConfig
from len_bot.adapters.file_upload import FileUploadConfig


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)
    onebot_file_upload: FileUploadConfig | None = None
    file_delivery: FileDeliveryConfig = Field(default_factory=FileDeliveryConfig)
    bot_qq: int = Field(gt=0, description="The Bot's QQ account ID")
    ws_host: str = Field(description='Reverse WebSocket host')
    ws_port: int = Field(description='Reverse WebSocket port')
    onebot_connection_mode: Literal['reverse_ws', 'forward_ws']
    onebot_action_transport: Literal['websocket', 'http']
    onebot_ws_url: str
    onebot_http_url: str
    onebot_access_token: str
    onebot_credential_revision: int = Field(default=1, ge=1, description='服务端令牌世代；replace/clear 必须与当前值一致')
    db_path: str = Field(description='Path to SQLite database')
    debounce_idle_ms: int = Field(description='Sliding idle window (ms)')
    debounce_max_ms: int = Field(description='Max debounce wait cap (ms)')
    addressed_debounce_idle_ms: int = Field(default=400, gt=0,
        description='真实 @ / 回复的短合并等待（毫秒）')
    addressed_debounce_max_ms: int = Field(default=1000, gt=0,
        description='真实 @ / 回复单批最大合并等待（毫秒），不承诺模型和送达延迟')
    observing_debounce_idle_ms: int = Field(default=800, gt=0,
        description='短时观察期内新原话的合并等待（毫秒）')
    observing_debounce_max_ms: int = Field(default=2000, gt=0,
        description='短时观察期内单批最大合并等待（毫秒）')
    conversation_max_steps: int | None = Field(ge=1,
        description='每轮对话的模型调用上限；null 表示该维度不设限，此时必须有期限或 token 上限')
    conversation_max_tool_calls: int | None = Field(ge=1,
        description='每轮对话的工具调用上限；null 表示该维度不设限')
    conversation_context_tokens: int = Field(ge=4000)
    conversation_output_tokens: int = Field(ge=256)
    conversation_recent_tokens: int = Field(ge=500)
    conversation_window_step_rowids: int = Field(default=200, ge=0,
        description='原话窗口起点按事件 rowid 向上取整到该步长，只在跨过一个步长时整体前移一次；0 表示不锚定，窗口每来一条消息就滑一格（那样跨轮缓存前缀会停在第一条历史消息）')
    conversation_window_seconds: float | None = Field(default=None, gt=0,
        description='一轮对话自首次模型调用起的绝对期限（秒）；null 表示不设期限维度；恢复不重置')
    attention_keywords: list[str]
    attention_observation_interval_seconds: float = Field(gt=0,
        description='普通消息待观察批次的间隔（秒）；有未读输入才定时触发，按容量分批提供，睡眠、权限与预算仍生效')
    attention_observation_enabled: bool = Field(
        description='是否启用普通消息的周期观察；关闭不影响真实搭话、短时观察期和名称/关键词的独立机会')
    attention_keyword_cooldown_seconds: float = Field(ge=0)
    attention_focus_seconds: float = Field(gt=0,
        description='真实搭话或本轮有来源的继续观察决定所授予的短时观察期（秒）；沉默可保留，普通消息和Bot发言不自动续期')
    attention_opportunity_ttl_seconds: float = Field(default=600.0, gt=0,
        description='旧版未记录读取范围的机会来源的保留秒数；新版未覆盖原话不因超时冒充已读，明确请求仍须处理结果')
    scene_hourly_message_limit: int = Field(default=0, ge=0,
        description='同一群每滚动小时真实发出的消息上限；达到后闲聊与主动发言停止进入模型，插件命令与推送不受影响；0 表示不限')
    user_hourly_message_limit: int = Field(default=0, ge=0,
        description='同一发起者每滚动小时收到的消息上限；达到后该发起者的闲聊停止进入模型；0 表示不限')
    max_context_images: int = Field(ge=1, le=6)
    work_output_tokens: int = Field(ge=256)
    jobs_enabled: bool
    heartbeat_enabled: bool = Field(default=False, description='系统心跳；默认关闭，不补跑错过的槽')
    heartbeat_topics: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20, description='明确的公共研究主题；空且无公共兴趣时允许零研究，不从群史生成主题')
    job_max_steps: int | None = Field(ge=1,
        description='同一工作累计模型调用上限；null 表示该维度不设限，由期限与 token 上限停止')
    job_max_tool_calls: int | None = Field(ge=1,
        description='同一工作累计工具调用上限；null 表示该维度不设限')
    job_max_seconds: float = Field(gt=0,
        description='同一工作自首次执行起的绝对期限（秒）；次数不设限时由它停止')
    job_context_tokens: int = Field(ge=4000)
    job_compress_trigger: float = Field(gt=0, lt=1)
    job_compress_target: float = Field(gt=0, lt=1)
    maintenance_context_tokens: int = Field(ge=4000)
    maintenance_output_tokens: int = Field(ge=256)
    history_target_tokens: int = Field(ge=100)
    history_min_tokens: int = Field(ge=1)
    history_quiet_window_seconds: float = Field(gt=0)
    job_max_concurrent: int = Field(ge=1)
    job_progress_interval_seconds: float = Field(ge=0, description='同一工作进展再次报告前的最短间隔')
    open_loop_ttl_seconds: float = Field(gt=0, description='待回应事项期限；表达提交时起算，真实送达后激活')
    media_enabled: bool
    message_pacing: bool
    maintenance_max_steps: int = Field(ge=1,
        description='一次历史维护的模型调用上限；该循环没有期限维度，它是必需的停止条件')
    maintenance_max_tool_calls: int | None = Field(ge=0,
        description='一次历史维护的工具调用上限；null 表示该维度不设限')
    maintenance_interval_seconds: float = Field(description='Background maintenance of explicit open-loop expiry')
    identity_name: str
    address_names: list[AddressName] = Field(max_length=32, description='额外呼唤昵称；只提供参与线索，不强制回复')
    character_context: str
    identity_core: str
    identity_persona: str
    conversation_style: str
    dashboard_enabled: bool = Field(description='Whether to run the management web dashboard')
    dashboard_host: str = Field(description='Dashboard HTTP bind host')
    dashboard_port: int = Field(description='Dashboard HTTP port (default 11307)')
    dashboard_secret_key: str
    dashboard_default_admin_user: str
    dashboard_default_admin_password: str
    dashboard_cookie_secure: bool = Field(description='Whether session cookie requires HTTPS')
    conversation_max_concurrent: int = Field(ge=1)
    scheduler_interval_seconds: float = Field(gt=0)
    media_max_image_bytes: int = Field(gt=0)
    media_max_file_bytes: int = Field(default=100_000_000, gt=0, description='视频/音频临时文件的最大字节数')
    media_max_image_pixels: int = Field(gt=0)
    media_max_dimension: int = Field(gt=0)
    media_context_max_bytes: int = Field(default=3_000_000, gt=0,
        description='一次请求装配的图片编码字节上限；超出时从最旧的图片开始移出窗口（0 以外的正数，token 估算看不见字节，这是唯一的字节维度）')
    media_request_timeout_seconds: float = Field(gt=0)
    media_io_concurrency: int = Field(ge=1)
    media_palette_limit: int = Field(ge=1)
    tool_result_page_chars: int = Field(ge=1)
    tool_result_max_chars: int = Field(ge=1)
    retrieval_default_limit: int = Field(ge=1, description='原话与认识检索未指定页量时读取的记录数')
    retrieval_max_limit: int = Field(ge=1, description='一次原话检索允许读取的最大记录数')
    read_context_default_neighbors: int = Field(ge=0, description='消息上下文默认读取前后各多少条原话')
    read_context_max_neighbors: int = Field(ge=0, description='消息上下文前后每侧允许读取的最大原话数')
    pending_wakes_default_limit: int = Field(ge=1, description='待处理来源定位页的默认记录数')
    pending_wakes_max_limit: int = Field(ge=1, description='待处理来源定位页的最大记录数')
    tool_discovery_limit: int = Field(ge=1, description='一次工具发现最多返回的工具数')
    conversation_summary_limit: int = Field(ge=1, description='对话装配时取回的近期历史摘要候选数')
    conversation_outbound_limit: int = Field(ge=1, description='对话查询最新发送状态时读取的批准表达批次数')
    media_search_limit: int = Field(ge=1, description='一次素材搜索取回的记录数')
    tool_read_concurrency: int = Field(ge=1)
    conversation_read_batch_limit: int = Field(ge=1)
    conversation_history_limit: int = Field(ge=1)
    onebot_request_timeout_seconds: float = Field(gt=0)
    onebot_probe_timeout_seconds: float = Field(gt=0)
    onebot_reconnect_seconds: float = Field(gt=0)
    action_max_concurrent: int = Field(ge=1)
    onebot_reconnect_max_seconds: float = Field(gt=0)
    onebot_ping_interval_seconds: float = Field(gt=0)
    onebot_ping_timeout_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def budgets_fit(self):
        if self.tool_result_page_chars > self.tool_result_max_chars:
            raise ValueError("tool_result_page_chars must not exceed tool_result_max_chars")
        if self.retrieval_default_limit > self.retrieval_max_limit:
            raise ValueError("retrieval_default_limit must not exceed retrieval_max_limit")
        if self.read_context_default_neighbors > self.read_context_max_neighbors:
            raise ValueError("read_context_default_neighbors must not exceed read_context_max_neighbors")
        if self.pending_wakes_default_limit > self.pending_wakes_max_limit:
            raise ValueError("pending_wakes_default_limit must not exceed pending_wakes_max_limit")
        if self.conversation_output_tokens >= self.conversation_context_tokens:
            raise ValueError("conversation_output_tokens must leave input capacity")
        if self.work_output_tokens >= self.job_context_tokens:
            raise ValueError("work_output_tokens must leave input capacity")
        if self.maintenance_output_tokens >= self.maintenance_context_tokens:
            raise ValueError("maintenance_output_tokens must leave input capacity")
        if self.job_compress_target >= self.job_compress_trigger:
            raise ValueError("job_compress_target must be less than job_compress_trigger")
        # An unlimited count is a real choice, but only when something else
        # genuinely stops the run.  A loop with no call limit and no deadline
        # has no stopping condition at all, so the file refuses that
        # combination instead of starting one.
        if self.conversation_max_steps is None and self.conversation_window_seconds is None:
            raise ValueError("conversation_max_steps 与 conversation_window_seconds 不能同时为 null；"
                             "对话次数不设限时必须给出绝对期限")
        if self.conversation_max_tool_calls is None and self.conversation_window_seconds is None:
            raise ValueError("conversation_max_tool_calls 与 conversation_window_seconds 不能同时为 null；"
                             "工具次数不设限时必须给出绝对期限")
        return self
