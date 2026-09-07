"""Shared native-tool loop; only an accepted terminal proposal ends a run."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from len_bot.cognition.gateway import ModelGateway, ToolCall


class ModelArgumentError(ValueError):
    def __init__(self, message: str, *, model_message: str | None = None):
        super().__init__(message)
        self.model_message = model_message or message


class TerminalArgumentError(ModelArgumentError):
    """The terminal proposal needs a model-correctable parameter repair."""


class ToolArgumentError(ModelArgumentError):
    """A nonterminal tool rejected its arguments before applying a proposal."""


class CommitConflict(RuntimeError):
    """The observed state changed; a proposal must not be repaired or sent."""


class FreshInputConflict(CommitConflict):
    """An uncommitted terminal saw new input; continue within this run budget."""


class AgentProtocolError(RuntimeError):
    pass


class AgentBudgetExhausted(RuntimeError):
    pass


class TruncatedModelOutput(AgentProtocolError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, default=str).encode()).hexdigest()


def _audit(value: Any, key: str = "") -> Any:
    """Keep useful arguments and candidates without credential/media payloads."""
    lowered = key.lower()
    if any(word in lowered for word in ("signature", "api_key", "password", "secret", "authorization", "base64")) or lowered in {"token", "access_token", "refresh_token"}:
        return {"redacted": True, "sha256": _digest(value)}
    if isinstance(value, dict):
        return {name: _audit(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_audit(item) for item in value]
    if isinstance(value, str) and (";base64," in value or re.fullmatch(r"[A-Za-z0-9+/=]{256,}", value)):
        return {"redacted": True, "sha256": _digest(value)}
    return value


def _error_text(exc: Exception) -> str:
    # Validation details belong in the tool result; avoid recording provider
    # signatures or base64 input snippets in the persistent audit trail.
    message = re.sub(r"data:[^\s]+;base64,[A-Za-z0-9+/=]+", "[media payload omitted]", str(exc))
    message = re.sub(r"[A-Za-z0-9+/=]{256,}", "[encoded payload omitted]", message)
    message = re.sub(
        r"(?i)((?:['\"])?(?:signature|api_key|password|secret|authorization|access_token|refresh_token)(?:['\"])?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[omitted]", message,
    )
    return f"{type(exc).__name__}: {message[:1500]}"


class AgentLoop:
    def __init__(self, gateway: ModelGateway):
        self.gateway = gateway

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_definitions: Callable[[], list[dict[str, Any]]],
        execute_tool: Callable[[str, dict[str, Any]], Awaitable[Any]],
        terminal: dict[str, Any] | Callable[[], dict[str, Any]],
        finish: Callable[[dict[str, Any]], Awaitable[Any]],
        proposal_tool_names: set[str] | frozenset[str] = frozenset(),
        max_steps: int = 5,
        max_tool_calls: int = 6,
        before_model: Callable[[], Awaitable[None]] | None = None,
        before_tool: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        observe: Callable[[], Awaitable[list[dict[str, Any]] | None]] | None = None,
        checkpoint: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        prepare_request: Callable[[list[dict], list[dict]], Awaitable[list[dict] | None]] | None = None,
        exchange_checkpoint: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        remaining_steps: Callable[[], Awaitable[int]] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> Any:
        if max_steps < 1 or max_tool_calls < 0:
            raise ValueError("Invalid agent run budget")
        terminal_name = (terminal() if callable(terminal) else terminal)["function"]["name"]
        trajectory = copy.deepcopy(messages)
        audit = trace if trace is not None else {}
        audit.update({"steps": [], "model_calls_used": 0, "tool_calls_used": 0,
                      "contract_repairs": [], "latency_ms": 0})
        repair_used = False
        tool_calls_used = 0

        async def repair(error: Exception, calls: tuple[ToolCall, ...], step: dict,
                         receipts: dict[str, Any] | None = None) -> None:
            nonlocal repair_used
            step["failure_reason"] = _error_text(error)
            audit["failure_reason"] = step["failure_reason"]
            if repair_used or audit["model_calls_used"] >= max_steps:
                raise AgentProtocolError(step["failure_reason"]) from error
            repair_used = True
            audit["contract_repairs"].append({"step": step["step"], "reason": step["failure_reason"]})
            feedback = getattr(error, "model_message", str(error))
            if calls:
                for call in calls:
                    result = (receipts or {}).get(call.id, {"error": "invalid_arguments", "message": feedback})
                    trajectory.append({"role": "tool", "tool_call_id": call.id,
                                       "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)})
            else:
                trajectory.append({"role": "user", "content": f"请使用原生工具调用完成本轮：{feedback}"})
            if calls and exchange_checkpoint is not None:
                await exchange_checkpoint(copy.deepcopy(trajectory))

        async def execute(call: ToolCall, arguments: dict[str, Any], item: dict) -> Any:
            nonlocal tool_calls_used
            if before_tool is not None:
                await before_tool(call.name, arguments)
            tool_calls_used += 1
            audit["tool_calls_used"] = tool_calls_used
            try:
                result = await execute_tool(call.name, arguments)
            except ToolArgumentError as exc:
                item["failure_reason"] = _error_text(exc)
                item["status"] = "invalid_arguments"
                raise
            except Exception as exc:
                item["failure_reason"] = _error_text(exc)
                item["status"] = "error"
                if call.name in proposal_tool_names:
                    raise
                return {"error": type(exc).__name__, "message": str(exc)}
            item["status"] = "completed"
            item["result_sha256"] = _digest(result)
            try:
                observation = json.loads(result) if isinstance(result, str) else result
            except (ValueError, TypeError):
                observation = None
            if isinstance(observation, dict) and observation.get('status') in {'ok','partial','no_results','error','unsupported'}:
                item['observation']={key:observation[key] for key in ('status','error_code','coverage','result_id') if key in observation}
            return result

        for step_index in range(max_steps):
            remaining = await remaining_steps() if remaining_steps is not None else max_steps - step_index
            if remaining < 1:
                raise AgentBudgetExhausted("No persistent model budget remains")
            forced_final = step_index == max_steps - 1 or tool_calls_used >= max_tool_calls or remaining == 1
            definitions = [] if forced_final else copy.deepcopy(tool_definitions())
            definitions = [definition for definition in definitions if definition["function"]["name"] != terminal_name]
            definitions.append(copy.deepcopy(terminal() if callable(terminal) else terminal))
            known_names = {definition["function"]["name"] for definition in definitions}
            choice: str | dict = {"type": "function", "function": {"name": terminal_name}} if forced_final else "required"
            if forced_final:
                trajectory.append({"role": "user", "content": (
                    f"本轮已经到最后一步，当前只开放 {terminal_name}。"
                    "请现在直接调用这个终结工具，提交最终结果；尚未核实的内容保留不确定性。"
                )})
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
                    ending = {"role": "user", "content": f"本工作剩余最后一次模型调用，请调用 {terminal_name}，保留未核实事项。"}
                    trajectory.append(ending)
                    if request_messages is not None and request_messages is not trajectory:
                        request_messages.append(copy.deepcopy(ending))
            if before_model is not None:
                await before_model()
            audit["model_calls_used"] += 1
            step = {"step": step_index, "provider_id": self.gateway.binding.provider_id,
                    "model": self.gateway.binding.model, "role": self.gateway.binding.role,
                    "forced_final": forced_final, "available_tools": [item["function"]["name"] for item in definitions],
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
            step.update({"latency_ms": response.latency_ms, "usage": _audit(response.usage),
                         "call_id": response.call_id, "local_estimate": response.local_estimate,
                         "finish_reason": response.finish_reason,
                         "continuation_keys": list(response.continuation),
                         "continuation_sha256": _digest(response.continuation)})
            audit["latency_ms"] += response.latency_ms
            if response.finish_reason == "length":
                step["failure_reason"] = "Model output truncated; candidate discarded"
                audit["failure_reason"] = step["failure_reason"]
                raise TruncatedModelOutput(step["failure_reason"])
            if response.finish_reason not in {"stop", "tool_calls"}:
                step["failure_reason"] = f"Incomplete model response: {response.finish_reason}"
                audit["failure_reason"] = step["failure_reason"]
                raise AgentProtocolError(step["failure_reason"])
            trajectory.append(response.continuation)
            calls = response.tool_calls
            parsed: list[tuple[ToolCall, dict[str, Any], dict[str, Any]]] = []
            argument_errors: list[ToolArgumentError] = []
            for call in calls:
                item = {"id": call.id, "name": call.name}
                step["tool_calls"].append(item)
                try:
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Expected an object")
                except (TypeError, ValueError):
                    item["arguments"] = {"invalid_json_object": True, "sha256": _digest(call.arguments)}
                    item["status"] = "invalid_arguments"
                    argument_errors.append(ToolArgumentError(f"Arguments for {call.name} must be a valid JSON object"))
                else:
                    item["arguments"] = _audit(arguments)
                    parsed.append((call, arguments, item))
                if call.name == terminal_name:
                    step["terminal_candidate"] = item["arguments"]
                    step.setdefault("terminal_candidates", []).append(item["arguments"])
            try:
                if not calls:
                    raise TerminalArgumentError(f"本轮必须调用 {terminal_name}；普通正文不会发送")
                if argument_errors:
                    raise argument_errors[0]
                for call in calls:
                    if call.name not in known_names:
                        raise ToolArgumentError(f"Unknown tool: {call.name}")
                terminal_calls = [entry for entry in parsed if entry[0].name == terminal_name]
                if len(terminal_calls) > 1:
                    raise TerminalArgumentError("Only one terminal call is allowed per response")
                if terminal_calls and any(call.name != terminal_name and call.name not in proposal_tool_names for call in calls):
                    raise TerminalArgumentError("Terminal and read tools cannot occur in the same response")
                external_count = len(calls) - len(terminal_calls)
                if external_count > max_tool_calls - tool_calls_used:
                    raise ToolArgumentError("Tool execution budget exceeded; finish with the observations already available")
                if forced_final and not terminal_calls:
                    raise TerminalArgumentError(f"No model budget remains; call {terminal_name}")
            except (TerminalArgumentError, ToolArgumentError) as exc:
                await repair(exc, calls, step)
                continue
            if checkpoint is not None:
                await checkpoint("after_model", copy.deepcopy(step))

            # Read tools may run concurrently. Proposal calls remain ordered and
            # are only staged; the terminal is resolved after all are staged.
            executions = [entry for entry in parsed if entry[0].name != terminal_name]
            results: list[Any] = []
            receipts: dict[str, Any] = {}
            try:
                if any(call.name in proposal_tool_names for call, _, _ in executions):
                    for call, arguments, item in executions:
                        result = await execute(call, arguments, item)
                        results.append(result)
                        receipts[call.id] = result
                else:
                    results = await asyncio.gather(*(execute(call, arguments, item) for call, arguments, item in executions),
                                                   return_exceptions=True)
                    receipts = {entry[0].id: result for entry, result in zip(executions, results)
                                if not isinstance(result, BaseException)}
                    failures = [result for result in results if isinstance(result, BaseException)]
                    if failures:
                        raise failures[0]
            except ToolArgumentError as exc:
                await repair(exc, calls, step, receipts)
                continue
            except Exception as exc:
                step["failure_reason"] = _error_text(exc)
                audit["failure_reason"] = step["failure_reason"]
                raise
            for (call, _, _), result in zip(executions, results):
                trajectory.append({"role": "tool", "tool_call_id": call.id,
                                   "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)})
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
                    if observe is not None:
                        additions = await observe()
                        if additions:
                            trajectory.extend(copy.deepcopy(additions))
                            audit["interim_batches"] = audit.get("interim_batches", 0) + 1
                    if exchange_checkpoint is not None:
                        await exchange_checkpoint(copy.deepcopy(trajectory))
                    continue
                except TerminalArgumentError as exc:
                    terminal_trace["status"] = "invalid_arguments"
                    # Existing proposal receipts stay in the trajectory. Only
                    # the terminal call needs a matching rejection receipt.
                    await repair(exc, (terminal_calls[0][0],), step)
                    continue
                except Exception as exc:
                    terminal_trace["status"] = "rejected"
                    step["failure_reason"] = _error_text(exc)
                    audit["failure_reason"] = step["failure_reason"]
                    raise
                terminal_trace["status"] = "accepted"
                audit.pop("failure_reason", None)
                return outcome
            if exchange_checkpoint is not None:
                await exchange_checkpoint(copy.deepcopy(trajectory))
            if observe is not None:
                additions = await observe()
                if additions:
                    trajectory.extend(copy.deepcopy(additions))
                    audit["interim_batches"] = audit.get("interim_batches", 0) + 1
        raise AgentBudgetExhausted("Model-step budget exhausted before an accepted terminal proposal")
