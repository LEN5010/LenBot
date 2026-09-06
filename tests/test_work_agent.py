import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from openai import AsyncOpenAI

from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.providers import RouteResolution
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.runtime.job_runner import InformationJobRunner, JobContextExhausted, WorkGateway
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.tools.results import ToolResult


def native_call(name, arguments, call_id="call"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def completion(*calls, finish_reason="tool_calls", **extensions):
    return {"choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": None, "tool_calls": list(calls), **extensions}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def facts(request):
    content = request["messages"][1]["content"]
    return json.loads(content if isinstance(content, str) else content[0]["text"])


def result_ids(request):
    observed = list(facts(request)["result_ids"])
    for message in request["messages"]:
        if message["role"] == "tool":
            result = json.loads(message["content"])
            if result.get("result_id"):
                observed.append(result["result_id"])
    return list(dict.fromkeys(observed))


def finish_response(request, **changes):
    result = {"summary": "已核对资料", "result_ids": result_ids(request), "unresolved": []}
    result.update(changes)
    return completion(native_call("finish_work", result))


class FixturePluginHost:
    def __init__(self, lookup=None):
        self.lookup = lookup
        self.called = []

    def get_tool_definitions(self):
        return [{"type": "function", "function": {"name": name, "description": name,
                "parameters": {"type": "object", "properties": {}}}} for name in ("fixture_read", "fixture_send")]

    def tool_capabilities(self, name):
        return {"read_only": name == "fixture_read", "deferred": False}

    def has_tool(self, name):
        return name in {"fixture_read", "fixture_send"}

    async def execute_tool(self, name, arguments):
        self.called.append(name)
        assert name == "fixture_read", "Worker must never execute a sending capability"
        return await self.lookup(arguments) if self.lookup else ToolResult(content="可靠的公开资料", evidence_kind="external")


class FixtureMedia:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []

    async def prepare_context_images(self, scene_id, assets, limit=6):
        assets = list(dict.fromkeys(assets))
        self.calls.append((scene_id, assets, limit))
        selected = assets[:limit]
        return {"blocks": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cGl4ZWxz"}} for _ in selected],
                "manifest": [{"asset_id": asset, "block_index": index} for index, asset in enumerate(selected)]}


class FixtureRegistry:
    def __init__(self, client):
        self.client = client
        self.calls = []
        self.model = "frozen-work-model"

    def resolve(self, role):
        self.calls.append(role)
        return RouteResolution("fixture", self.model, self.client, "high", role)


@pytest_asyncio.fixture
async def work_runtime(tmp_path):
    resources = []

    async def make(responder, *, lookup=None, **config):
        store = EventStore(str(tmp_path / f"work-{len(resources)}.db"), clock=lambda: 1000)
        await store.initialize()
        requests = []

        async def transport(request):
            body = json.loads(request.content)
            requests.append(body)
            data = await responder(body, len(requests))
            return httpx.Response(200, json=data)

        client = AsyncOpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        runtime = SimpleNamespace(event_store=store, config=RuntimeConfig(db_path=store.db_path, bot_qq=999, **config),
                                  memory_store=None, plugin_host=FixturePluginHost(lookup), evaluation_hook=None,
                                  provider_registry=FixtureRegistry(client), metrics=RuntimeMetrics(), events=[])

        async def commit(event):
            runtime.events.append(event)
            await store.append_event(event)

        runtime.commit_tool_observation = commit
        runtime.media_service = FixtureMedia(runtime)
        runtime.job_runner = InformationJobRunner(runtime)
        resources.append((runtime, client))
        return runtime, requests

    yield make
    for runtime, client in resources:
        await runtime.job_runner.stop()
        await client.close()
        await runtime.event_store.close()


async def create_job(runtime, *, quoted_image=False):
    store = runtime.event_store
    if quoted_image:
        await store.append_event(Event(id="quoted", event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id="group:work", actor_id="user:2", payload={"message_id": "123", "raw_text": "原图"},
            metadata={"media": [{"asset_id": "quoted-image"}]}))
    source = Event(id="source", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:work", actor_id="user:1",
                   payload={"raw_text": "查清楚这件事", **({"reply_to_message_id": "123"} if quoted_image else {})})
    await store.append_event(source)
    proposal = JobProposal(proposal_id="work", goal="核对原始资料", source_event_ids=["source"])
    async with store._write_lock:
        await store._db.execute("BEGIN IMMEDIATE")
        _, refs = await store.apply_job_proposals_in_transaction([proposal], "group:work", "test-episode", "shadow")
        await store._db.commit()
    job_id = refs["work"]
    await store.mark_task_status(job_id, "processing")
    return job_id


async def control_job(runtime, job_id, operation, constraints=()):
    store = runtime.event_store
    job = await store.get_job(job_id, "group:work")
    proposal = JobProposal(operation=operation, job_id=job_id, expected_revision=job["revision"],
                           source_event_ids=["source"], constraints_add=list(constraints))
    async with store._write_lock:
        await store._db.execute("BEGIN IMMEDIATE")
        await store.apply_job_proposals_in_transaction([proposal], "group:work", "test-control", "shadow")
        await store._db.commit()


@pytest.mark.asyncio
async def test_native_work_progress_returns_evidence_without_sending(work_runtime):
    async def respond(request, number):
        names = {tool["function"]["name"] for tool in request["tools"]}
        assert "finish_work" in names and "fixture_send" not in names
        terminal = next(tool["function"]["parameters"] for tool in request["tools"] if tool["function"]["name"] == "finish_work")
        assert set(terminal["properties"]) == set(terminal["required"]) == {"summary", "result_ids", "unresolved"}
        assert terminal["additionalProperties"] is False
        assert "response_format" not in request and request["reasoning_effort"] == "high"
        assert request["max_completion_tokens"] == 16384
        if number == 1:
            return completion(native_call("fixture_read", {}))
        ids = result_ids(request)
        return completion(native_call("report_progress", {"summary": "发现明确的来源", "result_ids": ids}, "progress"),
                          native_call("finish_work", {"summary": "核对完成", "result_ids": ids, "unresolved": []}, "finish"))

    runtime, requests = await work_runtime(respond)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert (job["model_steps"], job["tool_calls"], job["status"]) == (2, 2, "result_ready")
    assert job["result"]["status"] == "completed" and job["origin_mode"] == "shadow"
    assert len(requests) == 2 and runtime.provider_registry.calls == ["work"]
    assert {event.event_type for event in runtime.events}.issuperset({EventType.AGENT_JOB_PROGRESS, EventType.AGENT_JOB_FINISHED})
    assert all(event.event_type != EventType.MESSAGE_SENT for event in runtime.events)


@pytest.mark.asyncio
async def test_final_work_conclusion_with_unresolved_preserves_partial_results(work_runtime):
    async def respond(request, number):
        if number == 1:
            return completion(native_call("fixture_read", {}))
        assert request["tool_choice"]["function"]["name"] == "finish_work"
        return finish_response(request, summary="已核对官方文档的模型说明", unresolved=["具体正确率尚未核实"])

    runtime, requests = await work_runtime(respond, job_max_steps=2)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["status"] == "result_ready" and len(requests) == 2
    assert job["result"] == {"status": "partial", "summary": "已核对官方文档的模型说明",
                             "result_ids": job["result_ids"], "unresolved": ["具体正确率尚未核实"]}
    assert len(job["result_ids"]) == 1
    trace = (await runtime.event_store.query_traces(ref_id=job_id))[0]
    assert trace["kind"] == "agent_job" and trace["payload"]["runs"][0]["contract_repairs"] == []


@pytest.mark.asyncio
async def test_work_terminal_rejects_an_extra_status_instead_of_converting_it(work_runtime):
    async def respond(request, number):
        return finish_response(request, status="completed", unresolved=["尚未完成"])

    runtime, requests = await work_runtime(respond, job_max_steps=1)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["result"]["status"] == "failed" and len(requests) == 1
    trace = (await runtime.event_store.query_traces(ref_id=job_id))[0]
    assert "Extra inputs are not permitted" in trace["payload"]["runs"][0]["failure_reason"]


@pytest.mark.asyncio
async def test_work_output_configuration_reaches_the_model_request(work_runtime):
    async def respond(request, number):
        assert request["max_completion_tokens"] == 8192
        return finish_response(request)

    runtime, requests = await work_runtime(respond, work_output_tokens=8192)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["result"]["status"] == "completed" and len(requests) == 1


@pytest.mark.asyncio
async def test_work_context_reserves_the_full_configured_output_before_request(work_runtime):
    async def respond(request, number):
        raise AssertionError("Input plus output reservation exceeds the context budget")

    runtime, requests = await work_runtime(respond, job_context_tokens=8000, work_output_tokens=6144)
    gateway = WorkGateway(runtime.provider_registry.resolve("work"), runtime.config.job_context_tokens,
                          runtime.config.work_output_tokens)
    with pytest.raises(JobContextExhausted):
        await gateway.complete([{"role": "user", "content": "x" * 6000}], [], "required")
    assert requests == []


@pytest.mark.asyncio
async def test_revision_rebuilds_facts_preserves_results_budget_and_frozen_model(work_runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def lookup(arguments):
        started.set()
        await release.wait()
        return ToolResult(content="已取得的来源", evidence_kind="external")

    async def respond(request, number):
        assert request["model"] == "frozen-work-model"
        if number == 1:
            return completion(native_call("fixture_read", {}), vendor_signature="old-trajectory")
        assert facts(request)["constraints"] == ["只用手机"]
        assert facts(request)["result_ids"]
        assert "old-trajectory" not in json.dumps(request)
        return finish_response(request)

    runtime, requests = await work_runtime(respond, lookup=lookup)
    job_id = await create_job(runtime)
    task = asyncio.create_task(runtime.job_runner._run_job(job_id, "group:work"))
    await asyncio.wait_for(started.wait(), 3)
    await control_job(runtime, job_id, "revise", ["只用手机"])
    runtime.provider_registry.model = "new-profile-must-not-be-used"
    release.set()
    await asyncio.wait_for(task, 3)
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert (job["revision"], job["model_steps"], job["tool_calls"], job["status"]) == (2, 2, 1, "result_ready")
    assert runtime.plugin_host.called == ["fixture_read"]
    assert runtime.provider_registry.calls == ["work"] and len(requests) == 2


@pytest.mark.asyncio
async def test_cancellation_after_tool_stops_before_another_model_or_result(work_runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def lookup(arguments):
        started.set()
        await release.wait()
        return ToolResult(content="迟到的资料", evidence_kind="external")

    async def respond(request, number):
        assert number == 1
        return completion(native_call("fixture_read", {}))

    runtime, requests = await work_runtime(respond, lookup=lookup)
    job_id = await create_job(runtime)
    task = asyncio.create_task(runtime.job_runner._run_job(job_id, "group:work"))
    await asyncio.wait_for(started.wait(), 3)
    await control_job(runtime, job_id, "cancel")
    release.set()
    await asyncio.wait_for(task, 3)
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["status"] == "cancelled" and job["result"] is None
    assert len(requests) == 1 and job["model_steps"] == 1
    assert all(event.event_type != EventType.AGENT_JOB_FINISHED for event in runtime.events)


@pytest.mark.asyncio
async def test_work_quote_and_tool_attachments_are_native_and_deduplicated(work_runtime):
    async def lookup(arguments):
        return ToolResult(content="图中资料", attachments=["read-image", "read-image"], evidence_kind="external")

    async def respond(request, number):
        if number == 1:
            assert facts(request)["image_manifest"][0]["asset_id"] == "quoted-image"
            return completion(native_call("fixture_read", {}))
        images = [part for message in request["messages"] if isinstance(message.get("content"), list)
                  for part in message["content"] if part.get("type") == "image_url"]
        assert len(images) == 2
        if number == 2:
            return completion(native_call("read_tool_result", {"result_id": result_ids(request)[0]}))
        return finish_response(request)

    runtime, requests = await work_runtime(respond, lookup=lookup)
    job_id = await create_job(runtime, quoted_image=True)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["result"]["status"] == "completed" and len(requests) == 3
    assert [call[1] for call in runtime.media_service.calls] == [["quoted-image"], ["read-image"]]


@pytest.mark.asyncio
async def test_unobserved_result_id_is_repaired_once_before_result_commit(work_runtime):
    async def respond(request, number):
        return finish_response(request, result_ids=["foreign"] if number == 1 else [])

    runtime, requests = await work_runtime(respond)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["model_steps"] == 2 and job["result"]["result_ids"] == []
    assert len([event for event in runtime.events if event.event_type == EventType.AGENT_JOB_FINISHED]) == 1
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_failed_model_attempt_charged_once_without_fallback(work_runtime):
    async def respond(request, number):
        raise httpx.ReadTimeout("fixture timeout")

    runtime, requests = await work_runtime(respond)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["model_steps"] == 1 and len(requests) == 1
    assert job["status"] == "result_ready" and job["result"]["status"] == "failed"


@pytest.mark.asyncio
async def test_persisted_budget_exhaustion_returns_partial_without_model_call(work_runtime):
    async def respond(request, number):
        raise AssertionError("Budget exhausted before model")

    runtime, requests = await work_runtime(respond, job_max_steps=2)
    job_id = await create_job(runtime)
    await runtime.event_store.job_checkpoint(job_id, "group:work", 1, model_steps=2, tool_calls=3)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert (job["model_steps"], job["tool_calls"]) == (2, 3)
    assert job["result"]["status"] == "partial" and requests == []
