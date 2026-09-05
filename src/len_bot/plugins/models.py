from enum import StrEnum
from typing import Any, Optional, Callable, Awaitable
from pydantic import BaseModel, Field
import time

class PluginPermission(StrEnum):
    EMIT_EVENT = "emit_event"
    REGISTER_TOOL = "register_tool"
    INTERCEPT_ACTION = "intercept_action"

class PluginType(StrEnum):
    SENSORY = "sensory"
    TOOL = "tool"
    SCHEDULED = "scheduled"
    INTERCEPTOR = "interceptor"
    HYBRID = "hybrid"

class PluginManifest(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    plugin_type: PluginType = PluginType.HYBRID
    permissions: list[PluginPermission] = Field(default_factory=list)
    enabled: bool = True
    timeout_seconds: float = 5.0
    config: dict[str, Any] = Field(default_factory=dict)
    # ADR-0021 §15.1: declarative manifest surface for the Control Plane
    config_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema driving the config UI")
    default_config: dict[str, Any] = Field(default_factory=dict)
    emitted_events: list[str] = Field(default_factory=list)
    registered_tools: list[str] = Field(default_factory=list)

class PluginToolDefinition(BaseModel):
    plugin_id: str
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Any  # Callable[[dict[str, Any]], Awaitable[str]]
    timeout_seconds: float = 5.0
    read_only: bool = False
    deferred: bool = False
