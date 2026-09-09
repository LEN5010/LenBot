"""One call account shared by an execution and any plugin Agent it invokes."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class AgentBudget:
    model_limit: int
    tool_limit: int
    model_used: int = 0
    tool_used: int = 0
    on_model: Callable[[], Awaitable[None]] | None = None
    on_tool: Callable[[str, dict], Awaitable[None]] | None = None
    read_state: Callable[[], Awaitable[dict]] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def state(self) -> dict:
        if self.read_state:
            state = await self.read_state()
            self.model_used, self.tool_used = state['model_calls_used'], state['tool_calls_used']
            return state
        return {'model_calls_limit': self.model_limit, 'model_calls_used': self.model_used,
                'tool_calls_limit': self.tool_limit, 'tool_calls_used': self.tool_used}

    async def take_model(self) -> None:
        from len_bot.cognition.agent_loop import AgentBudgetExhausted
        async with self._lock:
            state = await self.state()
            if state['model_calls_used'] >= state['model_calls_limit']:
                raise AgentBudgetExhausted('The shared model budget is exhausted')
            if self.on_model:
                await self.on_model()
            self.model_used += 1

    async def take_tool(self, name: str, arguments: dict) -> None:
        from len_bot.cognition.agent_loop import AgentBudgetExhausted
        async with self._lock:
            state = await self.state()
            if state['tool_calls_used'] >= state['tool_calls_limit']:
                raise AgentBudgetExhausted('The shared tool budget is exhausted', budget_kind='tool_calls')
            if self.on_tool:
                await self.on_tool(name, arguments)
            self.tool_used += 1
