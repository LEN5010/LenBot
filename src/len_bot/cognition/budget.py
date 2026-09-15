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
    """The absolute monotonic instant one window closes, or None when it has none.

    A window is absolute from the run's first model call, so a resume passes
    the time already spent and keeps counting down from the same instant
    instead of receiving a fresh one.  The number returned is a
    `time.monotonic()` reading and is only meaningful inside one process; a
    deadline that has to outlive the process is stored as wall-clock seconds
    instead, the same basis every other persisted timestamp uses.
    """
    if seconds_limit is None:
        return None
    return time.monotonic() + max(0.0, seconds_limit - max(0.0, elapsed))


def seconds_left_to(deadline_at: float | None, now: float | None = None) -> float | None:
    """Seconds until one absolute wall-clock deadline, or None when there is none.

    A work stores the instant its window closes so the grant survives a queue
    wait, a restart or a wait for a reply.  Reading it against the current
    time is the whole computation; the deadline is one fact, not a duration
    that gets re-derived from accumulated counters.
    """
    if deadline_at is None:
        return None
    return deadline_at - (time.time() if now is None else now)


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

    work_token_limit: int | None = Field(default=None, gt=0,
        description='单个工作累计模型 token 上限；null 表示不设 token 维度上限')
    daily_user_token_limit: int | None = Field(default=None, ge=0,
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

    def work_reservation(self, *, model_steps: int | None, context_tokens: int, output_tokens: int) -> int | None:
        """What one work may hold, and therefore the most it may ever spend.

        The amount is the work's own configured limits multiplied out — every
        model step can at most fill the work context and the work output — so
        the hold is an upper bound of the configuration the work actually runs
        under rather than a second number that could disagree with it.  When
        the policy sets a smaller per-work ceiling, that ceiling is the hold.

        `None` means this work holds no token dimension: the deadline and the
        message limits are then its whole stopping condition.  That is only an
        honest answer when the day has no finite balance for it to overspend —
        a finite daily quota with no per-work ceiling has nothing to reserve
        against it, and is refused here rather than promised.  `None` is never
        zero: zero would read as "granted no tokens at all".
        """
        if model_steps is None:
            if self.work_token_limit is None and (self.daily_user_token_limit is not None
                                                 or self.daily_scene_token_limit is not None):
                raise ValueError(
                    '有限日额度要求有限单工作预占：请在额度策略中设置 work_token_limit，或为工作设置 job_max_steps；'
                    '否则该工作的消费无法在创建时计入日账')
            return self.work_token_limit
        ceiling = max(0, model_steps) * (max(0, context_tokens) + max(0, output_tokens))
        if self.work_token_limit is None:
            return ceiling
        return min(self.work_token_limit, ceiling)


def work_call_admission(store, job_id: str, *, now: Callable[[], float] | None = None):
    """The admission one work's model request passes before it is sent.

    Called from inside the call store's write transaction, so the account it
    reads already contains every earlier request's hold and this decision is
    what the next request will see.  It returns the amount to hold for the
    request, or refuses with `AgentBudgetExhausted` when the request cannot be
    paid for.

    Ceiling and deadline are read from the work's own snapshot at admit time,
    not captured when the gateway was constructed: the absolute deadline is
    written on first start, which can be after the gateway exists.
    """
    clock = now or time.time

    async def admit(input_estimate: dict, output_tokens: int) -> int | None:
        from len_bot.cognition.agent_loop import AgentBudgetExhausted
        ceiling, deadline_at = await store.work_budget_facts(job_id)
        if deadline_at is not None and deadline_at - clock() <= 0:
            raise AgentBudgetExhausted('The work reached its absolute deadline', budget_kind='elapsed_time')
        request_tokens = max(0, int(input_estimate.get('input_tokens') or 0)) + max(0, int(output_tokens))
        if ceiling is None:
            return None
        spent_usage, spent_estimate = await store.job_measured_tokens(
            job_id, conservative_output_tokens=output_tokens)
        spent = spent_usage + spent_estimate
        if spent + request_tokens > ceiling:
            raise AgentBudgetExhausted(
                f'本工作的累计 token 额度不足：已计入 {spent}，本次请求需要 {request_tokens}，上限 {ceiling}；'
                '已有结果和未完成项按原样保留，不再追加调用', budget_kind='tokens')
        return request_tokens

    return admit


class WorkBudgetSnapshot(BaseModel):
    """The creation facts a work keeps even after its hold is settled.

    A settled hold is what the work spent, not what it may spend.  These are
    the numbers it was actually granted: the cumulative ceiling, the execution
    limits the ceiling was derived from, and — once it first starts — the
    absolute instant its window closes.  The deadline is stored as a
    wall-clock instant because it has to survive the process.
    """
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    job_max_steps: int | None = Field(default=None, ge=1)
    job_max_tool_calls: int | None = Field(default=None, ge=1)
    job_max_seconds: float = Field(gt=0)
    job_context_tokens: int = Field(gt=0)
    work_output_tokens: int = Field(gt=0)
    maintenance_context_tokens: int = Field(gt=0)
    maintenance_output_tokens: int = Field(gt=0)
    token_limit: int | None = Field(default=None, ge=0)
    deadline_at: float | None = Field(default=None, gt=0)

    def runtime_values(self) -> dict:
        """The subset the work's own execution limits are rebuilt from."""
        return self.model_dump(exclude={'token_limit', 'deadline_at'})


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

        A work reports the absolute instant its window closes; a conversation
        run has only the account's own deadline.  Whichever exists is the one
        checked, so both loops stop on the same fact rather than on two
        different notions of "time left".
        """
        if state.get('deadline_at') is not None:
            return seconds_left_to(state.get('deadline_at'))
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

        The token side answers only for a ceiling that exists.  A work with no
        token dimension at all is not "nearly out of tokens"; its deadline and
        message limits are what end it, and reporting that it has no room left
        would end it early for a limit nobody set.
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
            seconds_left = self._seconds_left(state)
            if seconds_left is not None and seconds_left <= 0:
                raise AgentBudgetExhausted('The run reached its absolute deadline', budget_kind='elapsed_time')
            if count_remaining(state.get('tool_calls_limit'), state['tool_calls_used']) == 0:
                raise AgentBudgetExhausted('The shared tool budget is exhausted', budget_kind='tool_calls')
            if self.on_tool:
                await self.on_tool(name, arguments)
            self.tool_used += 1
