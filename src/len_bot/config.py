"""Runtime parameters parsed from the project root configuration."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AddressName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]

class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)
    bot_qq: int = Field(gt=0, description="The Bot's QQ account ID")
    ws_host: str = Field(description='Reverse WebSocket host')
    ws_port: int = Field(description='Reverse WebSocket port')
    onebot_connection_mode: Literal['reverse_ws', 'forward_ws']
    onebot_action_transport: Literal['websocket', 'http']
    onebot_ws_url: str
    onebot_http_url: str
    onebot_access_token: str
    db_path: str = Field(description='Path to SQLite database')
    debounce_idle_ms: int = Field(description='Sliding idle window (ms)')
    debounce_max_ms: int = Field(description='Max debounce wait cap (ms)')
    conversation_max_steps: int = Field(ge=1)
    conversation_max_tool_calls: int = Field(ge=1)
    conversation_context_tokens: int = Field(ge=4000)
    conversation_output_tokens: int = Field(ge=256)
    conversation_recent_tokens: int = Field(ge=500)
    attention_keywords: list[str]
    attention_sample_window_seconds: float = Field(gt=0)
    attention_sample_probability: float = Field(ge=0, le=1)
    attention_keyword_cooldown_seconds: float = Field(ge=0)
    attention_focus_seconds: float = Field(gt=0)
    max_context_images: int = Field(ge=1, le=6)
    work_output_tokens: int = Field(ge=256)
    jobs_enabled: bool
    job_max_steps: int = Field(ge=1)
    job_max_tool_calls: int = Field(ge=1)
    job_max_seconds: float = Field(gt=0)
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
    maintenance_max_steps: int = Field(ge=1)
    maintenance_max_tool_calls: int = Field(ge=0)
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
    media_max_image_pixels: int = Field(gt=0)
    media_max_dimension: int = Field(gt=0)
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
        return self
