"""Plugin-selected inputs; execution resources remain local to the host."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.cognition.budget import AgentBudget

if TYPE_CHECKING:
    from len_bot.cognition.context import ConversationContext
    from len_bot.cognition.mailbox import EpisodeMailbox
    from len_bot.cognition.models import EpisodeOutcome
    from len_bot.cognition.proposals import ProposalLedger
    from len_bot.tools.retrieval import RetrievalToolkit


RESULT_ONLY_NOTICE = '本次只通过return_result返回插件结果；不提供respond、工作或提醒提案，不自动发消息。'


def result_definition(output_model):
    return {'type':'function','function':{'name':'return_result','description':'返回结果给插件，不发送消息。',
        'parameters':output_model.model_json_schema()}}


class PluginAgentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    instructions: str = Field(min_length=1)
    tool_names: tuple[str, ...]
    model_role: Literal['conversation', 'work']
    max_steps: int | None = Field(default=None, ge=1,
        description='本次插件 Agent 的模型调用上限；null 表示由期限与父预算停止')
    max_tool_calls: int | None = Field(default=None, ge=0,
        description='本次插件 Agent 的工具调用上限；null 表示该维度不设限')
    context_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    include_identity: bool = False
    input_mode: Literal['materials', 'source', 'conversation'] = 'source'
    output_mode: Literal['result_only', 'respond'] = 'result_only'
    result_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def input_capacity(self):
        if self.output_tokens >= self.context_tokens:
            raise ValueError('Plugin Agent output reservation must be smaller than its context')
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError('Plugin Agent tools must be unique')
        if self.output_mode == 'respond' and self.input_mode == 'materials':
            raise ValueError('respond needs the real source projection to bind messages and proposals')
        return self


@dataclass
class PluginExecution:
    mailbox: EpisodeMailbox | None
    audit: dict = field(default_factory=dict)
    toolkit: RetrievalToolkit | None = None
    budget: AgentBudget | None = None
    context: ConversationContext | None = None
    ledger: ProposalLedger | None = None
    finish: Callable[..., Awaitable] | None = None
    after_finish: Callable[..., Awaitable] | None = None
    record_presentations: Callable[[list[dict]], Awaitable] | None = None
    model_slot_owned: bool = False
    agent_depth: int = 0
    agent_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    suspended_outcome: EpisodeOutcome | None = None
