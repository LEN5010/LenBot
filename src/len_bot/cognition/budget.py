"""One call account shared by an execution and any plugin Agent it invokes."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


class ReservationPolicy(BaseModel):
    """The operator's quota numbers and the day boundary they are counted on.

    This is also the root file's schema (`resources.policies.<name>`), so the
    numbers have exactly one editable home and a capability grant can point at
    them by name instead of copying a default into every layer.

    The billing day uses the project's existing business timezone: the plan
    asks for a billing timezone and does not ask for a second clock beside the
    one the calendar, the reports and the live cards already use.  A work is
    attributed to the day it was accepted and keeps that day until it settles,
    so work crossing midnight does not move into the next day's balance.

    `None` means that dimension is not limited, which is a real choice and not
    an error.  The plan requires a `None` mode to still have a genuine
    stopping condition, and the absolute deadline, the per-work ceiling and
    the message and concurrency limits are those conditions.
    """
    model_config = ConfigDict(extra='forbid', strict=True)

    work_token_limit: int | None = Field(default=10_000_000, ge=0,
        description='单个工作累计模型 token 上限；null 表示不设该维度上限')
    daily_user_token_limit: int | None = Field(default=30_000_000, ge=0,
        description='同一真实账号在账务日内的全局上限；null 表示不设该维度上限')
    daily_scene_token_limit: int | None = Field(default=None, ge=0,
        description='同一场景在账务日内的合计上限；null 表示本场景未配置该维度')

    def day_key(self, timestamp: float, timezone: str | None) -> str:
        """The billing day of one moment, in the project's business timezone.

        The timezone is passed in rather than stored here so the file keeps
        exactly one clock: `time.timezone` already governs the calendar, the
        reports and the live cards, and the plan asks for a billing timezone
        rather than for a second one beside it.
        """
        zone = ZoneInfo(timezone) if timezone else UTC
        return datetime.fromtimestamp(timestamp, zone).date().isoformat()

    def work_reservation(self, *, model_steps: int, context_tokens: int, output_tokens: int) -> int:
        """What one work may hold: the per-work ceiling, never more.

        The amount is the work's own existing hard limits multiplied out —
        every model step can at most fill the work context and the work output
        — so the hold is an upper bound of the configuration the work actually
        runs under rather than a second number that could disagree with it.
        A zero ceiling is still a real hold of zero: the work is allowed and
        its own step and deadline limits are what end it.
        """
        ceiling = max(0, model_steps) * (max(0, context_tokens) + max(0, output_tokens))
        if self.work_token_limit is None:
            return ceiling
        return min(self.work_token_limit, ceiling)


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
