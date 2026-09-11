"""Shared native-tool loop; only an accepted terminal proposal ends a run."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

from pydantic import ValidationError

from len_bot.cognition.gateway import ModelGateway, ToolCall
from len_bot.tools.results import ToolResult, error_message
from len_bot.tools.retrieval import ObservationPage
from len_bot.cognition.budget import AgentBudget

if TYPE_CHECKING:
    from len_bot.plugins.hooks import PluginRunHooks


class TerminalArgumentError(ValueError):
    """An uncommitted candidate can be corrected within the remaining budget."""

    def __init__(self, message, *, correction=None):
        super().__init__(message)
        self.correction = correction


class ToolArgumentError(ValueError):
    """A nonterminal tool rejected its arguments; return an error observation."""


class CommitConflict(RuntimeError):
    """The observed state changed; a proposal must not be repaired or sent."""


class FreshInputConflict(CommitConflict):
    """An uncommitted terminal saw new input; continue within this run budget."""


class AgentProtocolError(RuntimeError):
    pass


class AgentBudgetExhausted(RuntimeError):
    def __init__(self,message,*,budget_kind='model_steps'):
        super().__init__(message)
        self.budget_kind=budget_kind


class TruncatedModelOutput(AgentProtocolError):
    pass


def final_step_message(terminal_name: str) -> dict:
    return {"role": "developer", "_context_section": "terminal_hint", "content": (
        f"当前只开放 {terminal_name}，具体剩余额度见运行时记录。"
        "请直接调用这个提交工具，按其Schema选择本阶段结果与后续动作；尚未核实的内容保留不确定性。"
    )}


def execution_budget_message(state: dict[str, Any], terminal_name: str) -> tuple[dict, dict]:
    """The same runtime budget fact is used for initial packing and each call."""
    remaining = max(0, state['model_calls_limit'] - state['model_calls_used'] - 1)
    view = {**state, 'model_call_index': state['model_calls_used'] + 1,
            'model_calls_remaining_after': remaining,
            'tool_calls_remaining': max(0, state['tool_calls_limit'] - state['tool_calls_used']),
            'terminal_required': terminal_name}
    if 'elapsed_seconds_limit' in state:
        view['elapsed_seconds_remaining'] = max(0, round(state['elapsed_seconds_limit'] - state['elapsed_seconds_used'], 3))
    note = {'role': 'developer', '_context_section': 'execution_budget', 'content':
            '本次执行额度由运行时提供；优先推进当前请求的直接路径，无需额外读取时即可终结。'
            + ('本次已是最后一次模型调用，必须提交已有结果与缺口。' if remaining == 0
               else '后续至少留一次模型调用组织并提交终结。')
            + '终结不计普通工具次数。\n'
            + json.dumps(view, ensure_ascii=False)}
    return note, view


def _trace_value(value: Any, key: str = "") -> Any:
    """Keep useful arguments and candidates without credential/media payloads."""
    lowered = key.lower()
    if any(word in lowered for word in ("signature", "api_key", "password", "secret", "authorization", "base64")) or lowered in {"token", "access_token", "refresh_token"}:
        return {"omitted": True}
    if isinstance(value, dict):
        return {name: _trace_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_value(item) for item in value]
    if isinstance(value, str) and (";base64," in value or re.fullmatch(r"[A-Za-z0-9+/=]{256,}", value)):
        return {"omitted": True}
    return value


def _validation_cause(error):
    while error is not None:
        if isinstance(error, ValidationError):
            return error
        error = error.__cause__
    return None


def _error_text(exc: BaseException) -> str:
    # Validation details belong in the tool result; avoid recording provider
    # signatures or base64 input snippets in the persistent audit trail.
    validation = _validation_cause(exc)
    source = ('; '.join('.'.join(map(str,item['loc']))+': '+item['msg']
                       for item in validation.errors(include_input=False,include_context=False,include_url=False))
              if isinstance(validation,ValidationError) else str(exc))
    message = re.sub(r"data:[^\s]+;base64,[A-Za-z0-9+/=]+", "[media payload omitted]", source)
    message = re.sub(r"[A-Za-z0-9+/=]{256,}", "[encoded payload omitted]", message)
    message = re.sub(
        r"(?i)((?:['\"])?(?:signature|api_key|password|secret|authorization|access_token|refresh_token)(?:['\"])?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[omitted]", message,
    )
    return f"{type(exc).__name__}: {error_message(message)[:1500]}"


def _invalid_json_constant(value: str):
    raise ValueError(f'Non-JSON numeric constant: {value}')


def argument_failure(error):
    cause = _validation_cause(error)
    if isinstance(cause, ValidationError):
        return ToolResult.validation_failure(cause)
    return ToolResult.failure(_error_text(error), 'invalid_arguments', stage='references')


class AgentLoop:
    def __init__(self, gateway: ModelGateway):
        self.gateway = gateway

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_definitions: Callable[[], list[dict[str, Any]]],
        execute_tool: Callable[..., Awaitable[ToolResult | ObservationPage | dict[str, Any]]],
        terminal: dict[str, Any] | Callable[[], dict[str, Any]],
        finish: Callable[[dict[str, Any]], Awaitable[Any]],
        after_finish: Callable[[Any], Awaitable[dict[str, Any] | None]] | None = None,
        proposal_tool_names: set[str] | frozenset[str] = frozenset(),
        max_steps: int = 5,
        max_tool_calls: int = 6,
        before_model: Callable[[], Awaitable[None]] | None = None,
        before_tool: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        observe: Callable[[], Awaitable[list[dict[str, Any]] | None]] | None = None,
        checkpoint: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        prepare_request: Callable[[list[dict], list[dict]], Awaitable[list[dict] | None]] | None = None,
        prepare_tool_results: Callable[[list[dict], list[tuple[ToolCall, ToolResult | ObservationPage | dict[str, Any]]]], Awaitable[list[str]]] | None = None,
        exchange_checkpoint: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        remaining_steps: Callable[[], Awaitable[int]] | None = None,
        budget_state: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        finalize_request: Callable[[list[dict], list[dict]], Awaitable[list[dict] | None]] | None = None,
        record_tool_result: Callable[[ToolCall, dict, Any], Awaitable[Any]] | None = None,
        trace: dict[str, Any] | None = None,
        initial_model_calls: int = 0,
        initial_tool_calls: int = 0,
        budget: AgentBudget | None = None,
        hooks: PluginRunHooks | None = None,
        external_outcome: Callable[[], Any] | None = None,
    ) -> Any:
        if max_steps < 1 or max_tool_calls < 0 or not 0 <= initial_model_calls < max_steps or not 0 <= initial_tool_calls <= max_tool_calls:
            raise ValueError("Invalid agent run budget")
        terminal_name = (terminal() if callable(terminal) else terminal)["function"]["name"]
        account = budget or AgentBudget(max_steps, max_tool_calls, initial_model_calls, initial_tool_calls,
            on_model=before_model, on_tool=before_tool, read_state=budget_state)
        trajectory = copy.deepcopy(messages)
        audit = trace if trace is not None else {}
        audit.update({"steps": [], "model_calls_used": initial_model_calls, "tool_calls_used": initial_tool_calls, "latency_ms": 0})
        audit.pop('termination_reason', None)
        tool_calls_used = initial_tool_calls
        local_tools_used = 0
        seen_call_ids = {call['id'] for message in trajectory for call in (message.get('tool_calls') or [])}

        async def execute(call: ToolCall, arguments: dict[str, Any], item: dict) -> Any:
            nonlocal tool_calls_used, local_tools_used
            stopped = None
            try:
                if hooks is not None:
                    from len_bot.plugins.hooks import PluginHookStopped
                    try:
                        updated = await hooks.before_tool(call.name, arguments, call.id)
                        if updated != arguments:
                            item['effective_arguments'] = _trace_value(updated)
                            arguments = updated
                    except PluginHookStopped as error:
                        stopped = ToolResult.failure(str(error), 'plugin_stopped', stage='execution')
                await account.take_tool(call.name, arguments)
            except BaseException as exc:
                item['status'] = 'not_executed'
                item['failure_reason'] = _error_text(exc)
                raise
            local_tools_used += 1
            tool_calls_used = account.tool_used
            audit["tool_calls_used"] = tool_calls_used
            try:
                result = stopped if stopped is not None else await execute_tool(call.name, arguments, tool_call_id=call.id)
            except ToolArgumentError as exc:
                item["failure_reason"] = _error_text(exc)
                result = argument_failure(exc)
            except BaseException as exc:
                item["failure_reason"] = _error_text(exc)
                item["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                raise
            if record_tool_result is not None:
                result = await record_tool_result(call, arguments, result)
            state = await account.state()
            tool_calls_used = state['tool_calls_used']
            audit.update(model_calls_used=state['model_calls_used'], tool_calls_used=tool_calls_used)
            item["status"] = "stopped" if stopped is not None else "completed"
            record_result(item, result)
            return result

        def record_result(item, result):
            if isinstance(result, ObservationPage):
                result = result.result
            if isinstance(result, ToolResult):
                item['observation_status'] = result.status
                fields = {'status', 'error_code', 'coverage', 'result_id', 'observation_event_id'}
                if result.status in {'error','unsupported'}:
                    fields.update({'content', 'tool_name', 'tool_call_id', 'error_stage', 'error_details', 'http_status', 'sources', 'correction'})
                item['observation'] = result.model_dump(include=fields, exclude_none=True)
            elif isinstance(result, dict) and 'status' in result:
                item['receipt'] = {key: result[key] for key in ('status', 'proposal_ref', 'ack_ref', 'operation_ref') if key in result}

        async def install_budget(target):
            state = await account.state()
            audit.update(model_calls_used=state['model_calls_used'], tool_calls_used=state['tool_calls_used'])
            note, view = execution_budget_message(state, terminal_name)
            target[:] = [message for message in target if message.get('_context_section') != 'execution_budget']
            target.append(note)
            return view

        step = None
        try:
            for step_index in range(initial_model_calls,max_steps):
                step = None
                state = await account.state()
                remaining = await remaining_steps() if remaining_steps is not None else max_steps - step_index
                remaining = min(remaining, state['model_calls_limit'] - state['model_calls_used'])
                if remaining < 1:
                    raise AgentBudgetExhausted("No persistent model budget remains")
                forced_final = (step_index == max_steps - 1 or local_tools_used >= max_tool_calls-initial_tool_calls
                    or state['tool_calls_used'] >= state['tool_calls_limit'] or remaining == 1)
                definitions = [] if forced_final else copy.deepcopy(tool_definitions())
                definitions = [definition for definition in definitions if definition["function"]["name"] != terminal_name]
                definitions.append(copy.deepcopy(terminal() if callable(terminal) else terminal))
                known_names = {definition["function"]["name"] for definition in definitions}
                choice: str | dict = {"type": "function", "function": {"name": terminal_name}} if forced_final else "required"
                if forced_final and not any(message.get('_context_section') == 'terminal_hint' for message in trajectory):
                    trajectory.append(final_step_message(terminal_name))
                await install_budget(trajectory)
                request_messages = await prepare_request(trajectory, definitions) if prepare_request else None
                if remaining_steps is not None:
                    remaining = await remaining_steps()
                    if remaining < 1:
                        raise AgentBudgetExhausted("Context maintenance used the remaining model budget")
                    if remaining == 1 and not forced_final:
                        forced_final = True
                        definitions = [copy.deepcopy(terminal() if callable(terminal) else terminal)]
                        known_names = {terminal_name}
                        choice = {"type": "function", "function": {"name": terminal_name}}
                        ending = {"role": "developer", "content": f"本工作剩余最后一次模型调用，请调用 {terminal_name}，保留未核实事项。"}
                        trajectory.append(ending)
                        if request_messages is not None and request_messages is not trajectory:
                            request_messages.append(copy.deepcopy(ending))
                # Compression may have spent model calls. Rebuild the factual
                # budget and tool list from that same current accounting before
                # checking the final request, without asking another model.
                budget = await install_budget(trajectory)
                forced_final = forced_final or budget['model_calls_remaining_after'] == 0 or budget['tool_calls_remaining'] == 0
                definitions = [] if forced_final else copy.deepcopy(tool_definitions())
                definitions = [item for item in definitions if item['function']['name'] != terminal_name]
                definitions.append(copy.deepcopy(terminal() if callable(terminal) else terminal))
                known_names = {item['function']['name'] for item in definitions}
                choice = {'type': 'function', 'function': {'name': terminal_name}} if forced_final else 'required'
                if hooks is not None:
                    prepared, definitions = await hooks.before_model(
                        request_messages if request_messages is not None else trajectory, definitions, terminal_name)
                    trajectory[:] = prepared
                    request_messages = None
                    known_names = {item['function']['name'] for item in definitions}
                if finalize_request is not None:
                    request_messages = await finalize_request(trajectory, definitions)
                elif request_messages is not None and request_messages is not trajectory:
                    request_messages[:] = [item for item in request_messages if item.get('_context_section') != 'execution_budget']
                    request_messages.append(copy.deepcopy(trajectory[-1]))
                await account.take_model()
                audit["model_calls_used"] = account.model_used
                step = {"step": step_index, "provider_id": self.gateway.binding.provider_id,
                        "model": self.gateway.binding.model, "role": self.gateway.binding.role,
                        "forced_final": forced_final, "available_tools": [item["function"]["name"] for item in definitions],
                        "budget": budget,
                        "tool_calls": []}
                audit["steps"].append(step)
                if checkpoint is not None:
                    await checkpoint("before_model", {**step, "message_count": len(trajectory)})
                try:
                    response = await self.gateway.complete(request_messages if request_messages is not None else trajectory, definitions, choice)
                except Exception as exc:
                    step["failure_reason"] = _error_text(exc)
                    audit["failure_reason"] = step["failure_reason"]
                    raise
                step.update({"latency_ms": response.latency_ms, "usage": response.usage,
                             "call_id": response.call_id, "local_estimate": response.local_estimate,
                             "finish_reason": response.finish_reason,
                             "continuation_keys": list(response.continuation)})
                audit["latency_ms"] += response.latency_ms
                # Input was actually provided even if the returned protocol is
                # rejected below. No returned tool executes before validation.
                if checkpoint is not None:
                    await checkpoint("after_model", copy.deepcopy(step))
                if response.finish_reason == "length":
                    step["failure_reason"] = "Model output truncated; candidate discarded"
                    audit["failure_reason"] = step["failure_reason"]
                    raise TruncatedModelOutput(step["failure_reason"])
                if response.finish_reason not in {"stop", "tool_calls"}:
                    step["failure_reason"] = f"Incomplete model response: {response.finish_reason}"
                    audit["failure_reason"] = step["failure_reason"]
                    raise AgentProtocolError(step["failure_reason"])
                calls = response.tool_calls
                parsed: list[tuple[ToolCall, dict[str, Any], dict[str, Any]]] = []
                argument_errors: list[AgentProtocolError] = []
                for call in calls:
                    item = {"id": call.id, "name": call.name, "status": "not_executed"}
                    step["tool_calls"].append(item)
                    try:
                        arguments = json.loads(call.arguments, parse_constant=_invalid_json_constant)
                        if not isinstance(arguments, dict):
                            raise ValueError("Expected an object")
                    except (TypeError, ValueError):
                        item["arguments"] = {"invalid_json_object": True, "omitted": True}
                        item["status"] = "invalid_protocol"
                        argument_errors.append(AgentProtocolError(f"Arguments for {call.name} must be a valid JSON object"))
                    else:
                        item["arguments"] = _trace_value(arguments)
                        parsed.append((call, arguments, item))
                    if call.name == terminal_name:
                        step["terminal_candidate"] = item["arguments"]
                        step.setdefault("terminal_candidates", []).append(item["arguments"])
                try:
                    if not calls:
                        raise AgentProtocolError(f"本轮必须调用 {terminal_name}；普通正文不会发送")
                    if argument_errors:
                        raise argument_errors[0]
                    if any(not isinstance(call.id, str) or not call.id.strip() for call in calls):
                        raise AgentProtocolError('Every native tool call needs a nonempty ID')
                    if len({call.id for call in calls}) != len(calls) or any(call.id in seen_call_ids for call in calls):
                        raise AgentProtocolError('Duplicate native tool-call IDs')
                    for call in calls:
                        if call.name not in known_names:
                            raise AgentProtocolError(f"Unknown tool: {call.name}")
                    terminal_calls = [entry for entry in parsed if entry[0].name == terminal_name]
                    if len(terminal_calls) > 1:
                        raise AgentProtocolError("Only one terminal call is allowed per response")
                    if terminal_calls and len(calls) != 1:
                        raise AgentProtocolError('A terminal call must be in its own response, after every prior tool receipt')
                    external_count = len(calls) - len(terminal_calls)
                    state = await account.state()
                    if external_count > min(max_tool_calls-initial_tool_calls-local_tools_used,
                                            state['tool_calls_limit']-state['tool_calls_used']):
                        raise AgentBudgetExhausted("Tool execution budget exceeded; no calls in this response were executed",budget_kind='tool_calls')
                    if forced_final and not terminal_calls:
                        raise AgentProtocolError(f"Only {terminal_name} is available for this model call")
                except (TerminalArgumentError, AgentProtocolError, AgentBudgetExhausted) as exc:
                    step["failure_reason"] = _error_text(exc)
                    audit["failure_reason"] = step["failure_reason"]
                    raise
                if hooks is not None:
                    parsed = await hooks.after_model(parsed)
                    for call, arguments, item in parsed:
                        if _trace_value(arguments) != item['arguments']:
                            item['effective_arguments'] = _trace_value(arguments)
                    terminal_calls = [entry for entry in parsed if entry[0].name == terminal_name]
                trajectory.append(response.continuation)
                seen_call_ids.update(call.id for call in calls)
                # Read tools may run concurrently. Proposals and work-state updates
                # remain ordered; a later model response can use their returned refs.
                executions = [entry for entry in parsed if entry[0].name != terminal_name]
                results: list[Any] = []
                try:
                    if any(call.name in proposal_tool_names for call, _, _ in executions):
                        for call, arguments, item in executions:
                            result = await execute(call, arguments, item)
                            results.append(result)
                            if external_outcome is not None and external_outcome() is not None:
                                audit['termination_reason'] = 'plugin_wait_committed'
                                return external_outcome()
                    else:
                        results = await asyncio.gather(*(execute(call, arguments, item) for call, arguments, item in executions),
                                                       return_exceptions=True)
                        failures = [result for result in results if isinstance(result, BaseException)]
                        if failures:
                            raise failures[0]
                except Exception as exc:
                    step["failure_reason"] = _error_text(exc)
                    audit["failure_reason"] = step["failure_reason"]
                    raise
                if prepare_tool_results is not None and not terminal_calls:
                    results = await prepare_tool_results(trajectory, [(entry[0], result) for entry, result in zip(executions, results)])
                    if len(results) != len(executions):
                        raise AgentProtocolError("Tool presentation must retain every matching response")
                replies = []
                for (call, arguments, item), result in zip(executions, results):
                    # Page planning may reject a source range after trying
                    # several display sizes. Persist only its selected error,
                    # never the packer's tentative render attempts.
                    if isinstance(result, str) and record_tool_result is not None:
                        displayed = json.loads(result)
                        if isinstance(displayed, dict) and displayed.get('status') in {'error','unsupported'} and not displayed.get('result_id'):
                            failure = ToolResult.model_validate(displayed)
                            failure.error_stage = failure.error_stage or 'presentation'
                            result = await record_tool_result(call, arguments, failure)
                            record_result(item, result)
                            if isinstance(result, ObservationPage):result = result.result
                    if isinstance(result, ToolResult):
                        content = result.model_dump_json(exclude_none=True)
                    elif isinstance(result, str):
                        content = result
                    elif isinstance(result, dict):
                        content = json.dumps(result, ensure_ascii=False)
                    else:
                        raise AgentProtocolError("A stored observation requires presentation before the next model request")
                    if hooks is not None:
                        content = await hooks.after_tool(call.name, content, call.id)
                    replies.append({"role": "tool", "tool_call_id": call.id, "content": content})
                trajectory.extend(replies)
                if terminal_calls:
                    _, arguments, terminal_trace = terminal_calls[0]
                    try:
                        outcome = await finish(arguments)
                    except FreshInputConflict as exc:
                        terminal_trace["status"] = "fresh_input_conflict"
                        step["failure_reason"] = _error_text(exc)
                        trajectory.append({"role": "tool", "tool_call_id": terminal_calls[0][0].id,
                                           "content": json.dumps({"error": "fresh_input_conflict", "committed": False,
                                                                  "message": str(exc)}, ensure_ascii=False)})
                        if exchange_checkpoint is not None:
                            await exchange_checkpoint(copy.deepcopy(trajectory))
                        if step_index == max_steps - 1 or remaining == 1:
                            audit['termination_reason'] = 'final_step_fresh_input_conflict'
                            raise
                        if observe is not None:
                            additions = await observe()
                            if additions:
                                trajectory.extend(copy.deepcopy(additions))
                                audit["interim_batches"] = audit.get("interim_batches", 0) + 1
                        continue
                    except TerminalArgumentError as exc:
                        terminal_trace["status"] = "invalid_arguments"
                        step["failure_reason"] = _error_text(exc)
                        result = argument_failure(exc)
                        result.correction = exc.correction
                        if record_tool_result is not None:
                            result = await record_tool_result(terminal_calls[0][0], arguments, result)
                        record_result(terminal_trace, result)
                        if isinstance(result, ObservationPage):result = result.result
                        receipt = {'committed': False, **result.model_dump(mode='json', exclude_none=True)}
                        terminal_trace['committed'] = False
                        trajectory.append({'role': 'tool', 'tool_call_id': terminal_calls[0][0].id,
                                           'content': json.dumps(receipt, ensure_ascii=False)})
                        if exchange_checkpoint is not None:
                            await exchange_checkpoint(copy.deepcopy(trajectory))
                        if step_index == max_steps - 1 or remaining == 1:
                            audit['termination_reason'] = 'final_step_invalid_arguments'
                            raise
                        if observe is not None:
                            additions = await observe()
                            if additions:trajectory.extend(copy.deepcopy(additions))
                        continue
                    except Exception as exc:
                        terminal_trace["status"] = "rejected"
                        step["failure_reason"] = _error_text(exc)
                        audit["failure_reason"] = step["failure_reason"]
                        raise
                    terminal_trace["status"] = "accepted"
                    audit.pop("failure_reason", None)
                    if after_finish is not None:
                        terminal_trace['committed'] = True
                        # Publication follows durable acceptance. A later error
                        # must not rewrite this checkpoint as a rejected draft.
                        receipt=await after_finish(outcome)
                        if receipt is not None:
                            terminal_trace['receipt']=_trace_value(receipt)
                            trajectory.append({'role':'tool','tool_call_id':terminal_calls[0][0].id,
                                               'content':json.dumps(receipt,ensure_ascii=False)})
                            if receipt.get('continue_run'):
                                if exchange_checkpoint is not None:
                                    await exchange_checkpoint(copy.deepcopy(trajectory))
                                if observe is not None:
                                    additions=await observe()
                                    if additions:trajectory.extend(copy.deepcopy(additions))
                                continue
                    return outcome
                if exchange_checkpoint is not None:
                    await exchange_checkpoint(copy.deepcopy(trajectory))
                if observe is not None:
                    additions = await observe()
                    if additions:
                        trajectory.extend(copy.deepcopy(additions))
                        audit["interim_batches"] = audit.get("interim_batches", 0) + 1
            raise AgentBudgetExhausted("Model-step budget exhausted before an accepted terminal proposal")
        except BaseException as exc:
            audit["failure_reason"] = _error_text(exc)
            if step is not None:
                step["failure_reason"] = audit["failure_reason"]
            raise
