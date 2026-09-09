"""Business callbacks inside the existing work store and executor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from len_bot.cognition.jobs import JobResult, ResultPresentation
    from len_bot.events.store import EventStore
    from len_bot.tools.results import ToolNextCall, ToolResult
    from len_bot.plugins.models import PluginCallContext


@dataclass(frozen=True)
class PluginWorkContext:
    """One existing job's execution services; none of this is model input."""
    call: PluginCallContext
    revision: int
    parameters: BaseModel
    goal: str
    constraints: tuple[str,...]
    context_tokens: int
    output_tokens: int
    resume_from: dict | None
    progress: Callable[[], Awaitable[BaseModel]]
    save_progress: Callable[[BaseModel], Awaitable[BaseModel]]
    save_result: Callable[[str, ToolResult], Awaitable[ToolResult]]
    adopt_results: Callable[[list[str]], Awaitable[None]]
    budget: Callable[[], Awaitable[dict]]

    async def run_agent(self, *, instructions, input_observations, output_model):
        return await self.call.run_agent(instructions=instructions,input_observations=input_observations,
            output_model=output_model,output_mode='result_only',input_mode='materials',include_identity=False,
            tool_names=(),model_role='work',max_steps=1,max_tool_calls=0,
            context_tokens=self.context_tokens,output_tokens=self.output_tokens)

    async def input_tokens(self, instructions, material, output_model):
        """Use the request estimator; reserve the store's two generated IDs."""
        from len_bot.cognition.agent_loop import execution_budget_message, final_step_message
        from len_bot.cognition.call_store import estimate_request
        from len_bot.plugins.agent import RESULT_ONLY_NOTICE, result_definition
        from len_bot.tools.retrieval import RetrievalToolkit
        material=material.model_copy(update={'plugin_origin':self.call.origin,'tool_name':'plugin_work_result'})
        budget,_=execution_budget_message(await self.budget(),'return_result')
        messages=[{'role':'system','content':instructions},RetrievalToolkit.material_view(material),
            {'role':'developer','content':RESULT_ONLY_NOTICE},budget,final_step_message('return_result')]
        return estimate_request(messages,[result_definition(output_model)])['input_tokens']+128


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
    execute: Callable[[PluginWorkContext], Awaitable[JobResult]] | None = None
    needs_model: Callable[[dict], bool] | None = None
