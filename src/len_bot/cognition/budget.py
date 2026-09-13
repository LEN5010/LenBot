"""One call account shared by an execution and any plugin Agent it invokes."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


def count_remaining(limit: int | None, used: int) -> int | None:
    """What is left of one count dimension; `None` means this dimension is not a stop.

    Every loop asks this instead of subtracting the limit itself, so a
    dimension the operator left unlimited never turns into `None - int`, a
    comparison against `None`, or an accidental zero that reads as "exhausted".
    """
    return None if limit is None else max(0, limit - used)


def tightest(*bounds: int | None) -> int | None:
    """The smallest of the known bounds; `None` means that source does not bound the run."""
    known = [bound for bound in bounds if bound is not None]
    return min(known) if known else None


# The terminal proposal is itself a model call that must still fit inside the
# run's own limit: without a reserve, a run whose deadline arrives mid-read
# would stop with no result at all, and the plan's rule is that a run stops by
# submitting what it already established.  Thirty seconds is one minute of a
# half-hour work and one minute of a heartbeat round; a shorter window keeps a
# quarter of itself instead, so the reserve can never be the whole limit.
TERMINAL_SECONDS = 30.0


def terminal_seconds_reserve(seconds_limit: float | None) -> float:
    """How much of one absolute limit its own terminal submission keeps."""
    if seconds_limit is None:
        return 0.0
    return min(TERMINAL_SECONDS, seconds_limit / 4)


def window_deadline(seconds_limit: float | None, elapsed: float) -> float | None:
    """The absolute instant one window closes, or None when it has no window.

    A window is absolute from the run's first model call, so a resume passes
    the time already spent and keeps counting down from the same instant
    instead of receiving a fresh one.
    """
    if seconds_limit is None:
        return None
    return time.monotonic() + max(0.0, seconds_limit - max(0.0, elapsed))


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

    A `None` dimension is not limited, which is a real operator choice: the
    plan requires that mode to still have a genuine stopping condition, and
    the absolute deadline, the per-work ceiling and the message and
    concurrency limits are those conditions.  A per-work ceiling of zero is
    not one of the choices — zero would mean "granted no tokens at all",
    which no request can act on; "no ceiling" is written as `null`.
    """
    model_config = ConfigDict(extra='forbid', strict=True)

    work_token_limit: int | None = Field(default=10_000_000, gt=0,
        description='单个工作累计模型 token 上限；null 表示不设 token 维度上限')
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

    def work_reservation(self, *, model_steps: int | None, context_tokens: int, output_tokens: int) -> int:
        """What one work may hold — and therefore the most it may ever spend.

        The amount is the work's own existing hard limits multiplied out —
        every model step can at most fill the work context and the work output
        — so the hold is an upper bound of the configuration the work actually
        runs under rather than a second number that could disagree with it.

        When the count dimension is not limited (`model_steps is None`) the
        ceiling cannot be multiplied out, so the hold is the policy's own
        per-work number.  Only when the policy does not set one either is the
        hold zero, which means this work holds no token budget at all: the
        deadline and the message limits are then its whole stopping condition.
        """
        if model_steps is None:
            return self.work_token_limit or 0
        ceiling = max(0, model_steps) * (max(0, context_tokens) + max(0, output_tokens))
        if self.work_token_limit is None:
            return ceiling
        return min(self.work_token_limit, ceiling)


@dataclass
class AgentBudget:
    """The counts, the deadline and the token allowance one run may spend.

    A `None` count means that dimension is not what stops this run.  The
    deadline is an absolute `time.monotonic()` instant, and the token
    allowance is the cumulative model tokens the run holds; either one
    refuses the next model call once it is used up, so an operator who leaves
    the counts unlimited still gets a real stop rather than an endless loop.
    """

    model_limit: int | None
    tool_limit: int | None
    model_used: int = 0
    tool_used: int = 0
    on_model: Callable[[], Awaitable[None]] | None = None
    on_tool: Callable[[str, dict], Awaitable[None]] | None = None
    read_state: Callable[[], Awaitable[dict]] | None = None
    deadline: float | None = None
    terminal_token_reserve: int = 0
    terminal_seconds_reserve: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _last_durable: dict = field(default_factory=dict, init=False, repr=False)

    async def state(self) -> dict:
        if self.read_state:
            state = await self.read_state()
            self._last_durable = state
            self.model_used, self.tool_used = state['model_calls_used'], state['tool_calls_used']
            return state
        return self.local_state()

    def local_state(self) -> dict:
        """This account's counts without awaiting, for callers that decide synchronously.

        The counts are this account's own and are updated by every accepted
        call, so they are current either way.  A durable record's time and
        token dimensions are carried over from the most recent `state()` read,
        which is what a terminal decision made between two model calls has.
        """
        state = {'model_calls_limit': self.model_limit, 'model_calls_used': self.model_used,
                 'tool_calls_limit': self.tool_limit, 'tool_calls_used': self.tool_used}
        state.update({key: value for key, value in self._last_durable.items()
                      if key in {'elapsed_seconds_limit', 'elapsed_seconds_used',
                                 'tokens_limit', 'tokens_used'}})
        return state

    def _seconds_left(self, state: dict) -> float | None:
        """Seconds left before this run's own stop, or None when time is not one.

        A work reports its absolute deadline through the state snapshot; a
        conversation run has only the account's own deadline.  Whichever
        exists is the one checked, so both loops stop on the same fact rather
        than on two different notions of "time left".
        """
        if 'elapsed_seconds_limit' in state:
            return state['elapsed_seconds_limit'] - state['elapsed_seconds_used']
        return self.deadline_seconds()

    def _refusal(self, state: dict) -> tuple[str, str] | None:
        """The reason a further model call may not start, or None if one may."""
        seconds_left = self._seconds_left(state)
        if seconds_left is not None and seconds_left <= 0:
            return 'elapsed_time', 'The run reached its absolute deadline'
        if count_remaining(state.get('model_calls_limit'), state['model_calls_used']) == 0:
            return 'model_steps', 'The shared model budget is exhausted'
        tokens_limit = state.get('tokens_limit')
        if tokens_limit is not None and state.get('tokens_used', 0) >= tokens_limit:
            return 'tokens', 'The work used the whole model-token budget it holds'
        return None

    def deadline_seconds(self) -> float | None:
        return None if self.deadline is None else max(0.0, self.deadline - time.monotonic())

    def force_terminal(self, state: dict) -> bool:
        """Whether the next call must already be the terminal one.

        The terminal keeps its own output, its own model call and its own
        slice of whatever is left.  When the operator left the counts
        unlimited this is what still ends the run with a result rather than a
        bare stop: the deadline and the token allowance are its stopping
        conditions, and the last of that allowance is spent on submitting what
        was already established instead of on one more read.
        """
        if self.terminal_seconds_reserve:
            seconds_left = self._seconds_left(state)
            if seconds_left is not None and seconds_left <= self.terminal_seconds_reserve:
                return True
        remaining = count_remaining(state.get('model_calls_limit'), state['model_calls_used'])
        if remaining is not None and remaining <= 1:
            return True
        if count_remaining(state.get('tool_calls_limit'), state['tool_calls_used']) == 0:
            return True
        tokens_limit = state.get('tokens_limit')
        if tokens_limit is not None:
            if tokens_limit - state.get('tokens_used', 0) <= self.terminal_token_reserve:
                return True
        return False

    async def take_model(self) -> None:
        from len_bot.cognition.agent_loop import AgentBudgetExhausted
        async with self._lock:
            state = await self.state()
            refusal = self._refusal(state)
            if refusal is not None:
                raise AgentBudgetExhausted(refusal[1], budget_kind=refusal[0])
            if self.on_model:
                await self.on_model()
            self.model_used += 1

    async def take_tool(self, name: str, arguments: dict) -> None:
        from len_bot.cognition.agent_loop import AgentBudgetExhausted
        async with self._lock:
            state = await self.state()
            if count_remaining(state.get('tool_calls_limit'), state['tool_calls_used']) == 0:
                raise AgentBudgetExhausted('The shared tool budget is exhausted', budget_kind='tool_calls')
            if self.on_tool:
                await self.on_tool(name, arguments)
            self.tool_used += 1
