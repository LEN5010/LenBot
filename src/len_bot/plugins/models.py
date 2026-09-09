from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Awaitable, Literal, TYPE_CHECKING
from pydantic import BaseModel, Field
from len_bot.tools.results import ToolResult

if TYPE_CHECKING:
    from len_bot.cognition.proposals import ProposalLedger


@dataclass(frozen=True)
class PluginCallContext:
    scene_id: str
    requester_qq_uid: str | None
    now: float
    cutoff_rowid: int
    episode_id: str | None
    job_id: str | None
    role: Literal["conversation", "work"]
    ledger: ProposalLedger | None = None
    work_operation: str | None = None
    requester_qq_uids: tuple[str, ...] = ()
    tool_call_id: str | None = None

class PluginPermission(StrEnum):
    EMIT_EVENT = "emit_event"
    REGISTER_TOOL = "register_tool"

class PluginType(StrEnum):
    SENSORY = "sensory"
    TOOL = "tool"
    SCHEDULED = "scheduled"
    HYBRID = "hybrid"

class PluginManifest(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    plugin_type: PluginType = PluginType.HYBRID
    permissions: list[PluginPermission] = Field(default_factory=list)
    enabled: bool
    timeout_seconds: float | None
    config: dict[str, Any]
    # The configured plugin schema is also the control panel's editing surface.
    config_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema driving the config UI")
    emitted_events: list[str] = Field(default_factory=list)
    registered_tools: list[str] = Field(default_factory=list)

class PluginToolDefinition(BaseModel):
    plugin_id: str
    name: str
    description: str
    purpose: str
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    parameter_model: type[BaseModel]
    handler: Callable[[BaseModel, PluginCallContext], Awaitable[ToolResult | dict[str, Any]]]
    timeout_seconds: float
    kind: Literal["read", "proposal"]
    roles: tuple[Literal["conversation", "work"], ...]
    deferred: bool = False
    available: Callable[[PluginCallContext], bool] | None = None
