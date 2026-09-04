from pydantic import BaseModel, Field
import os

class RuntimeConfig(BaseModel):
    bot_qq: int = Field(default=12345678, description="The Bot's QQ account ID")
    ws_host: str = Field(default="127.0.0.1", description="Reverse WebSocket host")
    ws_port: int = Field(default=8080, description="Reverse WebSocket port")
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
    
    # Identity
    identity_name: str = "Len"
    identity_persona: str = (
        "你是 Len，一个在群聊中真实生活、有自己时间感和记忆的社会化成员。"
        "你言简意赅、风趣自然，只在确实相关或被呼唤时参与讨论，不需要每次都抢话。"
        "如果不值得多说，保持沉默（SILENCE）是最优秀的选择。"
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
