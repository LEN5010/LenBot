"""Business callbacks inside the existing work store and executor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from len_bot.cognition.jobs import JobResult, ResultPresentation
    from len_bot.events.store import EventStore
    from len_bot.tools.results import ToolNextCall, ToolResult


@dataclass(frozen=True)
class PluginWorkRevision:
    parameters: BaseModel
    goal: str


@dataclass(frozen=True)
class PluginWorkSpec:
    operation: str
    parameters_model: type[BaseModel]
    revision_model: type[BaseModel]
    progress_model: type[BaseModel]
    allow_learning: bool
    allowed_tools: tuple[str,...]
    input_cutoff: Callable[[BaseModel, int], int]
    revise: Callable[[BaseModel, BaseModel, int, float], PluginWorkRevision]
    new_progress: Callable[[EventStore, str, BaseModel, tuple[BaseModel, BaseModel] | None], Awaitable[BaseModel]]
    adopt_reads: Callable[[EventStore, dict, BaseModel, BaseModel, list[ResultPresentation]], Awaitable[BaseModel]]
    finalize: Callable[[BaseModel, BaseModel, JobResult], JobResult]
    continuation: Callable[[dict, ToolResult], ToolNextCall | None]
    project_progress: Callable[[BaseModel], dict]
