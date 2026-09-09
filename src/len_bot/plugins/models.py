from __future__ import annotations

from dataclasses import dataclass, field
import re
from enum import StrEnum
from typing import Any, Callable, Awaitable, Literal, TYPE_CHECKING
from pydantic import BaseModel, ConfigDict, Field
from len_bot.events.models import Event, EventType, PluginOrigin
from len_bot.tools.results import ToolResult

if TYPE_CHECKING:
    from len_bot.cognition.proposals import ProposalLedger
    from len_bot.plugins.base import PluginContext
    from len_bot.plugins.agent import PluginExecution


class EmptySceneConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


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
    source_event_id: str | None = None
    origin: PluginOrigin | None = None
    entry_origin: PluginOrigin | None = None
    entry: Literal['chat', 'handler', 'work'] = 'chat'
    event: Event | None = None
    plugin: PluginContext | None = field(default=None, repr=False, compare=False)
    execution: PluginExecution | None = field(default=None, repr=False, compare=False)
    read_slot_owned: bool = field(default=False, repr=False, compare=False)

    @property
    def scene_config(self) -> BaseModel | None:
        return self.plugin.scene_config(self.scene_id) if self.plugin else None

    async def invoke_tool(self, name: str, arguments: BaseModel | dict) -> ToolResult:
        return await self.plugin.invoke_tool(self, name, arguments)

    async def submit_message(self, segments, *, mention_all=False):
        return await self.plugin.submit_message(self, segments, mention_all=mention_all)

    async def save_image(self, png: bytes, description: str) -> str:
        return await self.plugin.save_image(self, png, description)

    async def run_agent(self, **options):
        return await self.plugin.run_agent(self, **options)

    async def stage_work(self, *, goal, request_source, evidence, parameters=None, constraints=(), result_refs=()):
        return await self.plugin.stage_work(self,goal=goal,request_source=request_source,evidence=evidence,
            parameters=parameters,constraints=constraints,result_refs=result_refs)

    async def read_request_source(self,reference: str):
        return await self.plugin.read_request_source(self,reference)


@dataclass(frozen=True)
class ExactText:
    words: tuple[str, ...]

    def __call__(self, call: PluginCallContext) -> bool:
        return call.event.raw_text.strip() in self.words


@dataclass(frozen=True)
class Command:
    word: str

    def __call__(self, call: PluginCallContext) -> bool:
        parts = call.event.raw_text.strip().split(maxsplit=1)
        return bool(parts and parts[0] == self.word)


@dataclass(frozen=True)
class RegexText:
    pattern: str
    _compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, '_compiled', re.compile(self.pattern))

    def __call__(self, call: PluginCallContext) -> bool:
        return self._compiled.search(call.event.raw_text) is not None


@dataclass(frozen=True)
class PluginHandlerDefinition:
    plugin_id: str
    id: str
    description: str
    match: Callable[[PluginCallContext], bool]
    handler: Callable[[PluginCallContext], Awaitable[None]]
    event_types: tuple[EventType, ...]
    sources: tuple[Literal['human', 'plugin_event', 'self_sent'], ...]
    priority: int
    consume: bool
    require_to_me: bool
    order: int
    available: Callable[[PluginCallContext], bool] | None = None
    validate: Callable[[PluginCallContext], Awaitable[None]] | None = None
    allow_mention_all: Callable[[PluginCallContext], bool] | None = None

    def record(self) -> dict:
        if isinstance(self.match, ExactText):
            matcher = {'type': 'exact', 'words': list(self.match.words)}
        elif isinstance(self.match, Command):
            matcher = {'type': 'command', 'word': self.match.word}
        elif isinstance(self.match, RegexText):
            matcher = {'type': 'regex', 'pattern': self.match.pattern}
        else:
            matcher = {'type': 'callable', 'name': self.match.__qualname__}
        return {'id': self.id, 'description': self.description, 'match': matcher,
                'event_types': [value.value for value in self.event_types],
                'sources': list(self.sources), 'priority': self.priority,
                'consume': self.consume, 'require_to_me': self.require_to_me}

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
    page_chars: int | None = Field(default=None,ge=1)
