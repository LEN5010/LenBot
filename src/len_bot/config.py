from typing import Literal

from pydantic import BaseModel, Field
import os

class RuntimeConfig(BaseModel):
    bot_qq: int = Field(default=12345678, description="The Bot's QQ account ID")
    ws_host: str = Field(default="127.0.0.1", description="Reverse WebSocket host")
    ws_port: int = Field(default=8080, description="Reverse WebSocket port")
    onebot_connection_mode: Literal["reverse_ws", "forward_ws"] = "reverse_ws"
    onebot_action_transport: Literal["websocket", "http"] = "websocket"
    onebot_ws_url: str = "ws://127.0.0.1:13001/"
    onebot_http_url: str = "http://127.0.0.1:13000/"
    onebot_access_token: str = Field(default_factory=lambda: os.getenv("ONEBOT_ACCESS_TOKEN", ""))
    db_path: str = Field(default="len_bot.db", description="Path to SQLite database")
    
    # LLM Settings
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    default_model: str = Field(default="deepseek-chat")
    deliberate_model: str = Field(default="deepseek-reasoner")
    
    # Ingestion & Debounce
    debounce_idle_ms: int = Field(default=800, description="Sliding idle window (ms)")
    debounce_max_ms: int = Field(default=2500, description="Max debounce wait cap (ms)")
    max_ingest_lag_seconds: int = Field(default=60, description="Events older than this skip stimulus")
    social_context_window_tokens: int = Field(
        default=200_000,
        description="Maximum estimated Social Core input context before oldest raw messages roll out",
    )
    social_output_reserve_tokens: int = Field(
        default=8_000,
        description="Context-window reserve for the structured cognition result",
    )

    # Reflection (ADR-0019): quiet-window trigger, replacing message-count triggers
    reflection_quiet_window_seconds: float = Field(
        default=150.0,
        description="Scene quiet time before micro-reflection fires on unreflected events"
    )
    maintenance_interval_seconds: float = Field(
        default=60.0,
        description="Background heartbeat: open-loop GC and memory decay"
    )

    # Ambient Cognition (ADR-0034): durable next-wake intents
    next_wake_min_interval_seconds: float = Field(
        default=60.0,
        description="Minimum delay for Social Core next-wake tasks; closer wakes are clamped"
    )
    
    # Identity (ADR-0038: layered persona — identity core + style line + adaptive
    # self state + group register + voice exemplars, not two static strings)
    identity_name: str = "Len"
    character_context: str = ""
    identity_core: str = (
        "行为倾向(可观察):\n"
        "- 熟人面前随意直接,常用短句和吐槽;对陌生人客气但保持距离,不主动套近乎。\n"
        "- 被@或被问具体问题时认真;纯闲聊里多数时候碎片化,经常只回一两个字或一个梗。\n"
        "- 幽默方式:接梗、反讽、装傻;不解释笑点。\n"
        "- 冲突方式:嘴硬但不上纲上线,偶尔自嘲收场。\n"
        "- 不主动给建议;别人吐槽时先站队或吐槽回去,而不是安慰。\n"
        "- 沉默是常态:没有想说的就不说。"
    )
    identity_persona: str = (
        "你是 Len，一个在群聊中真实生活、有自己时间感和记忆的社会化成员。"
        "你言简意赅、风趣自然，只在确实相关或被呼唤时参与讨论，不需要每次都抢话。"
        "如果不值得多说，保持沉默（SILENCE）是最优秀的选择。"
    )
    conversation_style: str = (
        "使用自然、简短、口语化的中文群聊表达。不要使用客服腔、报告腔或不必要的完整解释。"
    )

    # Web Dashboard Settings
    dashboard_enabled: bool = Field(
        default_factory=lambda: os.getenv("DASHBOARD_ENABLED", "true").lower() in ("true", "1", "yes"),
        description="Whether to run the management web dashboard"
    )
    dashboard_host: str = Field(default="127.0.0.1", description="Dashboard HTTP bind host")
    dashboard_port: int = Field(default=11307, description="Dashboard HTTP port (default 11307)")
    dashboard_secret_key: str = Field(
        default_factory=lambda: os.getenv("DASHBOARD_SECRET_KEY", "len-bot-secret-salt-change-in-production")
    )
    dashboard_default_admin_user: str = "admin"
    dashboard_default_admin_password: str = "lenbot123"
    dashboard_cookie_secure: bool = Field(default=False, description="Whether session cookie requires HTTPS")
