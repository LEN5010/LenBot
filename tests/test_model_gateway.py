import asyncio
import copy
import json

import httpx
import pytest
import pytest_asyncio
from openai import AsyncOpenAI

from len_bot.cognition.agent_loop import (
    AgentLoop,
    AgentProtocolError,
    CommitConflict,
    TerminalArgumentError,
    ToolArgumentError,
    TruncatedModelOutput,
)
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.providers import ModelProfile, ProviderConfig, ProviderRegistry, RouteResolution, RoutingConfig


def definition(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


TERMINAL = definition("finish_turn")


def call(name, arguments="{}", call_id="call_1", **extra):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}, **extra}


def response(*calls, content=None, finish_reason="tool_calls", **extensions):
    message = {"role": "assistant", "content": content, **extensions}
    if calls:
        message["tool_calls"] = list(calls)
    return {"id": "completion", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}


@pytest_asyncio.fixture
async def gateway_factory():
    clients = []

    def make(responses, *, effort="high"):
        requests = []
        remaining = copy.deepcopy(responses)

        async def handle(request):
            requests.append(json.loads(request.content))
            assert remaining, "Unexpected extra model call"
            return httpx.Response(200, json=remaining.pop(0))

        client = AsyncOpenAI(api_key="fixture-secret", base_url="https://fixture.invalid/v1", max_retries=0,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        clients.append(client)
        gateway = ModelGateway(RouteResolution("fixture", "test-model", client, effort), max_output_tokens=2048)
        return gateway, requests

    yield make
    for client in clients:
        await client.close()
    # Every request uses an in-memory transport; no database or real network is opened.


async def unused_execute(name, args):
    raise AssertionError(f"Unexpected tool: {name}")


async def identity_finish(arguments):
    return arguments


async def run(gateway, **kwargs):
    defaults = {"messages": [{"role": "user", "content": "接一下这个话题"}],
                "tool_definitions": lambda: [], "execute_tool": unused_execute,
                "terminal": TERMINAL, "finish": identity_finish, "max_steps": 3}
    defaults.update(kwargs)
    return await AgentLoop(gateway).run(**defaults)


@pytest.mark.asyncio
async def test_native_continuation_lossless_and_parallel_receipts_in_call_order(gateway_factory):
    arguments = '{ "query" : "原样", "second": 2, "first": 1 }'
    original = response(
        call("read", arguments, "slow", vendor_signature="call-signature"),
        call("read", '{"query":"另一个"}', "fast"),
        reasoning_content="opaque reasoning", reasoning_details=[{"type": "reasoning.encrypted", "data": "secret-signed-block"}],
        vendor_extension={"signature": "unknown-sig", "unrecognized": [1, {"keep": None}]},
    )
    gateway, requests = gateway_factory([original, response(call("finish_turn", '{"messages":[{"text":"好了"}]}'))])
    second_started = asyncio.Event()
    finished = []

    async def execute(name, args):
        if args["query"] == "原样":
            await second_started.wait()
            finished.append("slow")
            return "slow-result"
        second_started.set()
        finished.append("fast")
        return {"result": "fast-result"}

    trace = {}
    checkpoints = []

    async def save_exchange(trajectory):
        checkpoints.append(trajectory)

    outcome = await run(gateway, tool_definitions=lambda: [definition("read")], execute_tool=execute, trace=trace,
                        exchange_checkpoint=save_exchange)
    assert len(checkpoints) == 1
    assert checkpoints[0][1] == original["choices"][0]["message"]
    assert [item["tool_call_id"] for item in checkpoints[0] if item["role"] == "tool"] == ["slow", "fast"]
    assert outcome["messages"] == [{"text": "好了"}]
    assert finished == ["fast", "slow"]
    assert requests[1]["messages"][1] == original["choices"][0]["message"]
    assert requests[1]["messages"][1]["tool_calls"][0]["function"]["arguments"] == arguments
    assert [item["tool_call_id"] for item in requests[1]["messages"] if item["role"] == "tool"] == ["slow", "fast"]
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["reasoning_effort"] == "high"
    assert requests[0]["max_completion_tokens"] == 2048
    assert requests[0]["stream"] is False
    assert "response_format" not in requests[0] and "temperature" not in requests[0]
    stored = json.dumps(trace)
    for hidden in ("secret-signed-block", "unknown-sig", "call-signature", "opaque reasoning"):
        assert hidden not in stored
    assert trace["steps"][1]["terminal_candidate"] == outcome
    assert trace["tool_calls_used"] == 2


@pytest.mark.asyncio
async def test_plain_content_never_becomes_a_reply_and_final_step_forces_terminal(gateway_factory):
    gateway, requests = gateway_factory([
        response(content="这个普通正文不可发送", finish_reason="stop"),
        response(call("finish_turn", '{"messages":[]}'), content="这个旁白也不可发送"),
    ], effort=None)
    accepted = []

    async def finish(args):
        accepted.append(args)
        return args

    trace = {}
    assert await run(gateway, max_steps=2, tool_definitions=lambda: [definition("start_work")],
                     finish=finish, trace=trace) == {"messages": []}
    assert accepted == [{"messages": []}]
    assert requests[0]["tools"] == [definition("start_work"), TERMINAL]
    assert requests[1]["tools"] == [TERMINAL]
    assert requests[1]["tool_choice"] == {"type": "function", "function": {"name": "finish_turn"}}
    assert "reasoning_effort" not in requests[0]
    assert len(trace["contract_repairs"]) == 1
    assert "普通正文不可发送" not in json.dumps(trace, ensure_ascii=False)


@pytest.mark.asyncio
async def test_terminal_argument_repair_is_one_call_and_accounted(gateway_factory):
    gateway, requests = gateway_factory([response(call("finish_turn", '{"wrong":1}')),
                                         response(call("finish_turn", '{"messages":[]}'))])
    charges = []

    async def charge():
        charges.append("model")

    async def finish(args):
        if "messages" not in args:
            raise TerminalArgumentError("messages is required")
        return args

    trace = {}
    await run(gateway, finish=finish, before_model=charge, trace=trace)
    assert charges == ["model", "model"]
    assert trace["model_calls_used"] == 2
    assert trace["tool_calls_used"] == 0
    assert requests[1]["messages"][-1]["tool_call_id"] == "call_1"
    assert trace["steps"][0]["terminal_candidate"] == {"wrong": 1}


@pytest.mark.asyncio
async def test_second_parameter_error_stops_without_third_call(gateway_factory):
    gateway, requests = gateway_factory([response(call("finish_turn", '{"wrong":1}')),
                                         response(call("finish_turn", '{"wrong":2}'))])

    async def finish(args):
        raise TerminalArgumentError("invalid terminal")

    with pytest.raises(AgentProtocolError, match="invalid terminal"):
        await run(gateway, finish=finish, max_steps=5)
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [CommitConflict("social revision changed"), RuntimeError("commit failed")])
async def test_commit_failure_is_not_repaired(gateway_factory, error):
    gateway, requests = gateway_factory([response(call("finish_turn", '{"messages":[]}'))])

    async def finish(args):
        raise error

    trace = {}
    with pytest.raises(type(error), match=str(error)):
        await run(gateway, finish=finish, trace=trace)
    assert len(requests) == 1
    assert trace["contract_repairs"] == []
    assert str(error) in trace["failure_reason"]


@pytest.mark.asyncio
async def test_terminal_with_read_tool_rejects_entire_batch_before_execution(gateway_factory):
    gateway, _ = gateway_factory([
        response(call("finish_turn", '{"messages":[]}'), call("read", "{}", "read-1")),
        response(call("finish_turn", '{"messages":[]}')),
    ])
    trace = {}
    await run(gateway, tool_definitions=lambda: [definition("read")], trace=trace)
    assert trace["tool_calls_used"] == 0
    assert "read tools" in trace["contract_repairs"][0]["reason"]


@pytest.mark.asyncio
async def test_terminal_waits_for_ordered_proposals_even_when_listed_first(gateway_factory):
    gateway, _ = gateway_factory([response(call("finish_turn", '{"messages":[]}'),
                                          call("propose", '{"id":1}', "proposal-1"),
                                          call("propose", '{"id":2}', "proposal-2"))])
    order = []

    async def execute(name, args):
        order.append(args["id"])
        return {"staged": args["id"]}

    async def finish(args):
        assert order == [1, 2]
        order.append("commit")
        return args

    trace = {}
    await run(gateway, tool_definitions=lambda: [definition("propose")], execute_tool=execute,
              proposal_tool_names={"propose"}, finish=finish, trace=trace)
    assert order == [1, 2, "commit"]
    assert trace["tool_calls_used"] == 2


@pytest.mark.asyncio
async def test_proposal_failure_never_reaches_terminal_commit(gateway_factory):
    gateway, requests = gateway_factory([response(call("propose", "{}", "p"), call("finish_turn", '{"messages":[]}'))])

    async def execute(name, args):
        raise RuntimeError("proposal transaction refused")

    async def finish(args):
        raise AssertionError("must not commit")

    with pytest.raises(RuntimeError, match="transaction refused"):
        await run(gateway, tool_definitions=lambda: [definition("propose")], execute_tool=execute,
                  proposal_tool_names={"propose"}, finish=finish)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_terminal_repair_does_not_replay_successful_proposal(gateway_factory):
    gateway, requests = gateway_factory([
        response(call("propose", "{}", "p"), call("finish_turn", '{"wrong":1}', "f")),
        response(call("finish_turn", '{"messages":[]}', "f2")),
    ])
    staged = []

    async def execute(name, args):
        staged.append("p")
        return {"handle": "p"}

    async def finish(args):
        if "wrong" in args:
            raise TerminalArgumentError("messages is required")
        return args

    await run(gateway, tool_definitions=lambda: [definition("propose")], execute_tool=execute,
              proposal_tool_names={"propose"}, finish=finish)
    assert staged == ["p"]
    assert [item["tool_call_id"] for item in requests[1]["messages"] if item["role"] == "tool"] == ["p", "f"]


@pytest.mark.asyncio
async def test_length_discards_candidate_and_does_not_repair(gateway_factory):
    gateway, requests = gateway_factory([response(call("finish_turn", '{"messages":["truncated'), finish_reason="length")])

    async def finish(args):
        raise AssertionError("must discard truncated output")

    trace = {}
    with pytest.raises(TruncatedModelOutput):
        await run(gateway, finish=finish, trace=trace)
    assert len(requests) == 1
    assert trace["contract_repairs"] == []
    assert "terminal_candidate" not in trace["steps"][0]


@pytest.mark.asyncio
async def test_tool_boundary_observation_and_budget_hooks(gateway_factory):
    gateway, requests = gateway_factory([response(call("read", "{}")), response(call("finish_turn", '{"messages":[]}'))])
    calls = []

    async def charge_tool(name, args):
        calls.append((name, args))

    async def execute(name, args):
        return "observed"

    async def observe():
        return [{"role": "user", "content": "新增输入"}]

    trace = {}
    await run(gateway, tool_definitions=lambda: [definition("read")], execute_tool=execute,
              before_tool=charge_tool, observe=observe, max_tool_calls=1, trace=trace)
    assert calls == [("read", {})]
    assert requests[1]["messages"][-2]["content"] == "新增输入"
    assert "finish_turn" in requests[1]["messages"][-1]["content"]
    assert requests[1]["tools"] == [TERMINAL]
    assert requests[1]["tool_choice"]["function"]["name"] == "finish_turn"
    assert trace["interim_batches"] == 1


@pytest.mark.asyncio
async def test_read_argument_repair_preserves_successful_parallel_receipt(gateway_factory):
    gateway, requests = gateway_factory([response(call("read", '{"valid":true}', "ok"), call("read", "{}", "bad")),
                                         response(call("finish_turn", '{"messages":[]}'))])

    async def execute(name, args):
        if not args.get("valid"):
            raise ToolArgumentError("valid flag required")
        return {"found": "evidence"}

    await run(gateway, tool_definitions=lambda: [definition("read")], execute_tool=execute)
    receipts = [item for item in requests[1]["messages"] if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in receipts] == ["ok", "bad"]
    assert json.loads(receipts[0]["content"]) == {"found": "evidence"}
    assert json.loads(receipts[1]["content"])["error"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_invalid_terminal_json_is_auditable_without_storing_raw_payload(gateway_factory):
    gateway, _ = gateway_factory([response(call("finish_turn", '{"signature":"private",')),
                                  response(call("finish_turn", '{"messages":[]}'))])
    trace = {}
    await run(gateway, trace=trace)
    assert trace["steps"][0]["terminal_candidate"]["invalid_json_object"] is True
    assert "private" not in json.dumps(trace)
    assert "valid JSON object" in trace["steps"][0]["failure_reason"]


@pytest.mark.asyncio
async def test_terminal_candidate_and_error_trace_exclude_credentials_and_base64(gateway_factory):
    candidate = {"messages": [], "api_key": "candidate-key", "nested": {"signature": "signed-block"},
                 "image": "data:image/png;base64," + "A" * 600}
    gateway, _ = gateway_factory([response(call("finish_turn", json.dumps(candidate)))])

    async def finish(args):
        raise CommitConflict("invalid signature='error-signed-block' data:image/png;base64," + "B" * 600)

    trace = {}
    with pytest.raises(CommitConflict):
        await run(gateway, finish=finish, trace=trace)
    saved = json.dumps(trace)
    for hidden in ("candidate-key", "signed-block", "error-signed-block", "A" * 600, "B" * 600):
        assert hidden not in saved


@pytest.mark.asyncio
async def test_cancellation_hook_prevents_tool_execution_and_terminal(gateway_factory):
    gateway, requests = gateway_factory([response(call("read", "{}"))])

    async def before_tool(name, args):
        raise asyncio.CancelledError()

    trace = {}
    with pytest.raises(asyncio.CancelledError):
        await run(gateway, tool_definitions=lambda: [definition("read")], before_tool=before_tool, trace=trace)
    assert len(requests) == 1
    assert trace["tool_calls_used"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [
    (call("read"),),
    (call("finish_turn"), call("start_work", call_id="work")),
])
async def test_last_step_rejects_unoffered_tools_without_execution_or_commit(gateway_factory, calls):
    gateway, requests = gateway_factory([response(*calls)])
    trace = {}

    async def finish(args):
        raise AssertionError("must reject the entire response")

    with pytest.raises(AgentProtocolError, match="Unknown tool:"):
        await run(gateway, tool_definitions=lambda: [definition("read"), definition("start_work")],
                  proposal_tool_names={"start_work"}, finish=finish, max_steps=1, trace=trace)
    assert len(requests) == 1
    assert requests[0]["tools"] == [TERMINAL]
    assert requests[0]["tool_choice"]["function"]["name"] == "finish_turn"
    assert trace["tool_calls_used"] == 0


@pytest.mark.asyncio
async def test_provider_binding_freezes_model_effort_and_connection():
    registry = ProviderRegistry()
    first = ProviderConfig(id="p", base_url="https://first.invalid/v1", api_key="first-secret")
    profile = ModelProfile(provider_id="p", model="first", reasoning_effort="high")
    await registry.apply_update([first], RoutingConfig(conversation=profile, work=profile))
    bound = registry.resolve("conversation")
    # Mutating the caller-owned config must not change the published connection.
    first.base_url = "https://caller-mutation.invalid/v1"
    assert registry.resolve("conversation").client.base_url.host == "first.invalid"
    new = ModelProfile(provider_id="p", model="second", reasoning_effort="low")
    await registry.apply_update([ProviderConfig(id="p", base_url="https://second.invalid/v1", api_key="second-secret")],
                                RoutingConfig(conversation=new, work=new))
    updated = registry.resolve("conversation")
    assert (bound.model, bound.reasoning_effort, bound.client.base_url.host) == ("first", "high", "first.invalid")
    assert (updated.model, updated.reasoning_effort, updated.client.base_url.host) == ("second", "low", "second.invalid")
    assert registry.resolve("work").model == updated.model
    with pytest.raises(LookupError, match="maintenance.*not configured"):
        registry.resolve("maintenance")
    resumed = registry.resolve_profile(profile, role="work")
    assert resumed.model == "first" and resumed.client.base_url.host == "second.invalid"
    assert "first-secret" not in json.dumps(registry.snapshot())
    with pytest.raises(ValueError, match="Unknown model role"):
        registry.resolve("deliberate")
    await bound.client.close()
    await updated.client.close()


@pytest.mark.asyncio
async def test_provider_can_be_saved_before_profiles_are_selected():
    registry = ProviderRegistry()
    await registry.apply_update([ProviderConfig(id="p", base_url="https://fixture.invalid/v1", api_key="saved-key")], None)
    assert registry.export()["routing"] is None
    assert len(registry.snapshot()["providers"]) == 1
    assert registry.has_live_provider() is False
    with pytest.raises(LookupError, match="No model profiles configured"):
        registry.resolve("conversation")


@pytest.mark.asyncio
async def test_provider_requests_are_durable_once_and_cancellation_has_unknown_usage(tmp_path):
    from len_bot.events.store import EventStore

    store = EventStore(str(tmp_path / "calls.db"))
    await store.initialize()
    entered = asyncio.Event()
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await asyncio.Event().wait()
        body = response(call("finish_turn", '{"messages":[]}'))
        body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 5}
        return httpx.Response(200, json=body)

    client = AsyncOpenAI(api_key="fixture-secret", base_url="https://fixture.invalid/v1", max_retries=0,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    gateway = ModelGateway(RouteResolution("fixture", "test", client), call_store=store,
                           scene_id="group:accounting", episode_id="episode:accounting")
    try:
        trace = {}
        await run(gateway, trace=trace)
        record = (await store.list_model_calls())[0]
        assert record["id"] == trace["steps"][0]["call_id"]
        assert record["status"] == "completed" and record["usage"]["completion_tokens"] == 8
        pending = asyncio.create_task(run(gateway))
        await asyncio.wait_for(entered.wait(), 2)
        assert any(row["status"] == "unconfirmed" for row in await store.list_model_calls())
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await store.close()
        await store.initialize()
        rows = await store.list_model_calls("group:accounting")
        assert len(rows) == 2
        cancelled = next(row for row in rows if row["status"] == "cancelled")
        assert cancelled["usage"] is None and cancelled["ended_at"] is not None
        totals = (await store.get_model_call_totals("group:accounting"))[0]
        assert totals["calls"] == 2 and totals["cancelled"] == totals["unknown_usage"] == 1
        assert totals["completion_tokens"] == 8 and totals["reasoning_tokens"] == 5
    finally:
        await client.close()
        await store.close()
