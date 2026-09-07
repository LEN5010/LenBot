import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

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
from len_bot.tools.results import ToolResult, ToolSource


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
        return await self.lookup(arguments) if self.lookup else ToolResult(content="可靠的公开资料", sources=[ToolSource(url="https://fixture.invalid/source")], evidence_kind="external")


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

    def resolve_profile(self, profile, role="work"):
        self.calls.append(role)
        return RouteResolution(profile.provider_id, profile.model, self.client, profile.reasoning_effort, role)


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
            await runtime.event_store.append_event(event)

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


async def create_job(runtime, *, quoted_image=False, source_id="source", proposal_id="work", source_text="查清楚这件事"):
    store = runtime.event_store
    if quoted_image:
        await store.append_event(Event(id="quoted", event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id="group:work", actor_id="user:2", payload={"message_id": "123", "raw_text": "原图"},
            metadata={"media": [{"asset_id": "quoted-image"}]}))
    source = Event(id=source_id, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:work", actor_id="user:1",
                   payload={"raw_text": source_text, **({"reply_to_message_id": "123"} if quoted_image else {})})
    await store.append_event(source)
    proposal = JobProposal(proposal_id=proposal_id, goal="核对原始资料", source_event_ids=[source_id])
    async with store._write_lock:
        await store._db.execute("BEGIN IMMEDIATE")
        _, refs = await store.apply_job_proposals_in_transaction([proposal], "group:work", "test-episode", "shadow")
        await store._db.commit()
    job_id = refs[proposal_id]
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
        assert set(terminal["required"]) == {"summary", "result_ids", "unresolved"}
        assert {"work_state", "skill_candidate"}.issubset(terminal["properties"])
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
        return ToolResult(content="已取得的来源", sources=[ToolSource(url="https://fixture.invalid/source")], evidence_kind="external")

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
        return ToolResult(content="图中资料", attachments=["read-image", "read-image"], sources=[ToolSource(url="https://fixture.invalid/image")], evidence_kind="external")

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
    await runtime.event_store.save_media_file("read-image", "group:work", "fixture", "image/png", "/tmp/fixture.png", curated=True)
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


@pytest.mark.asyncio
async def test_complete_native_checkpoint_resumes_with_progress_binding_and_original_budget(work_runtime):
    waiting = asyncio.Event()
    never = asyncio.Event()

    async def respond(request, number):
        if number == 1:
            return completion(native_call("fixture_read", {}, "original-read"), vendor_signature="native-private-continuation")
        if number == 2:
            ids = result_ids(request)
            return completion(native_call("update_work_state", {"state": {
                "goal_revision": 1, "plan": ["读取资料", "核对条件"],
                "completed_steps": [{"step": "读取了来源", "result_ids": ids}],
                "key_result_ids": ids, "unresolved": ["尚需对齐条件"], "next_step": "核对条件"}}, "state"))
        if number == 3:
            waiting.set()
            await never.wait()
        assert request["model"] == "frozen-work-model"
        assert "native-private-continuation" in json.dumps(request)
        assert facts(request)["state_needs_revision"] is True
        assert facts(request)["work_state"]["completed_steps"][0]["result_ids"]
        from len_bot.runtime.work_context import validate_complete_exchanges
        validate_complete_exchanges(request["messages"])
        return finish_response(request)

    runtime, requests = await work_runtime(respond)
    job_id = await create_job(runtime, quoted_image=True)
    running = asyncio.create_task(runtime.job_runner._run_job(job_id, "group:work"))
    await asyncio.wait_for(waiting.wait(), 3)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    before = await runtime.event_store.get_job(job_id, "group:work")
    checkpoint = await runtime.event_store.read_job_checkpoint(job_id, "group:work")
    assert checkpoint["exchange_count"] == 2
    assert "native-private-continuation" in json.dumps(checkpoint)
    assert "base64" not in json.dumps(checkpoint)
    assert "native-private-continuation" not in json.dumps(before)
    assert before["model_steps"] == 3
    await runtime.event_store.close()
    runtime.event_store = EventStore(runtime.config.db_path, clock=lambda: 2000)
    await runtime.event_store.initialize()
    await runtime.event_store.recover_task_execution()
    assert (await runtime.event_store.get_job(job_id, "group:work"))["status"] == "review_required"
    runtime.provider_registry.model = "changed-default"
    await control_job(runtime, job_id, "resume")
    await runtime.event_store.mark_task_status(job_id, "processing")
    await runtime.job_runner._run_job(job_id, "group:work")
    after = await runtime.event_store.get_job(job_id, "group:work")
    assert after["result"]["status"] == "completed"
    assert after["model_steps"] == 4 and after["tool_calls"] == 2
    assert after["elapsed_seconds"] >= before["elapsed_seconds"]
    assert runtime.plugin_host.called == ["fixture_read"] and len(requests) == 4
    calls = await runtime.event_store.list_model_calls("group:work")
    assert len(calls) == 4 and sum(item["status"] == "cancelled" for item in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_valid", [True, False])
async def test_long_document_work_compresses_whole_exchanges_and_continues_reading(work_runtime, compression_valid):
    root = Path(__file__).parents[1]
    document = "\n".join((root / path).read_text() for path in (
        "docs/architecture.md", "docs/implementation.md", "docs/adr/0044-native-conversation-and-evidence-ledger.md"))
    work_calls = 0
    compression_seen = False
    read_after_compression = False

    async def lookup(arguments):
        return ToolResult(content=document, sources=[ToolSource(url="https://fixture.invalid/project-contracts")], evidence_kind="external")

    async def respond(request, number):
        nonlocal work_calls, compression_seen, read_after_compression
        names = {tool["function"]["name"] for tool in request["tools"]}
        if "summarize_work_segment" in names:
            compression_seen = True
            payload = json.loads(request["messages"][1]["content"])
            from len_bot.runtime.work_context import validate_complete_exchanges
            validate_complete_exchanges(payload["completed_exchanges"])
            ids = [json.loads(item["content"])["result_id"] for item in payload["completed_exchanges"] if item["role"] == "tool"]
            return completion(native_call("summarize_work_segment", {"summary": "已读取工程契约的第一段。事件为事实源，提案经事务和发送Gate；工作与送达分离。其余资料尚待读取。", "result_ids": ids if compression_valid else ["foreign-result"], "unresolved": ["继续阅读未覆盖的文档页"]}))
        work_calls += 1
        if work_calls == 1:
            return completion(native_call("fixture_read", {}, "source"), vendor_signature="original-source-signature")
        pages = [json.loads(item["content"]) for item in request["messages"] if item["role"] == "tool" and "result_id" in json.loads(item["content"])]
        latest = pages[-1]
        if latest.get("next_offset") is not None:
            read_after_compression |= compression_seen
            return completion(native_call("read_tool_result", {"result_id": latest["result_id"], "offset": latest["next_offset"], "limit": 5000}, f"page-{work_calls}"), vendor_signature=f"native-tail-{work_calls}")
        return finish_response(request, summary="文档已全部读取；事件、提案、执行预算和发送回执边界已核对")

    runtime, requests = await work_runtime(respond, lookup=lookup, job_context_tokens=24000, work_output_tokens=4096,
                                            job_compress_trigger=0.6, job_compress_target=0.4)
    job_id = await create_job(runtime)
    await runtime.job_runner._run_job(job_id, "group:work")
    job = await runtime.event_store.get_job(job_id, "group:work")
    assert job["model_steps"] == len(requests)
    assert (await runtime.event_store.read_tool_observation(job["result_ids"][0], ["group:work"])).content == document
    if not compression_valid:
        assert job["result"]["status"] == "partial"
        assert job["compression"]["status"] == "failed" and job["compression"]["segments"] == []
        native = await runtime.event_store.read_job_checkpoint(job_id, "group:work")
        assert "original-source-signature" in json.dumps(native)
        assert "旧工作区间摘要" not in json.dumps(native)
        assert not read_after_compression
        return
    assert job["result"]["status"] == "completed"
    assert compression_seen and read_after_compression
    assert job["compression"]["status"] == "ready" and job["compression"]["segments"]
    rows = await (await runtime.event_store._db.execute("SELECT messages_json FROM job_exchanges WHERE job_id=? ORDER BY sequence", (job_id,))).fetchall()
    assert "original-source-signature" in rows[0][0]
    calls = await runtime.event_store.list_model_calls("group:work")
    assert any(item["purpose"] == "work_compression" and item["job_id"] == job_id for item in calls)


@pytest.mark.asyncio
async def test_skill_candidate_learning_reading_correction_and_version_scope(work_runtime):
    work_calls = {}
    learned_id = None
    phase = 1

    async def respond(request, number):
        names = {tool["function"]["name"] for tool in request["tools"]}
        if "save_skill" in names:
            payload = json.loads(request["messages"][1]["content"])
            if phase == 2:
                assert payload["correction_originals"] and payload["existing_skill"]["version"] == 1
            return completion(native_call("save_skill", {"name": "算式核对", "applicability": "给定算式的结果核验",
                "steps": ["先检查用户给定的数字和单位", "使用 calculate 计算"],
                "verification": ["按逆运算复核"] if phase >= 2 else ["对齐计算结果"], "exclusions": ["不用于未确定输入的外部事实"]}))
        job_id = facts(request)["job_id"]
        work_calls[job_id] = work_calls.get(job_id, 0) + 1
        step = work_calls[job_id]
        if phase > 1 and step == 1:
            return completion(native_call("read_skill", {"skill_id": learned_id}))
        if (phase == 1 and step == 1) or (phase > 1 and step == 2):
            if phase > 1:
                skill = json.loads(json.loads(request["messages"][-1]["content"])["content"])
                assert skill["version"] == (1 if phase == 2 else 2)
                if phase == 3:
                    assert skill["verification"] == ["按逆运算复核"]
            return completion(native_call("calculate", {"expression": "7*8"}))
        changes = {}
        if phase < 3:
            changes["skill_candidate"] = {"name": "算式核对", "lesson": "计算应明确输入并保留可复查验证步骤", "result_ids": result_ids(request)}
            if phase == 2:
                changes["skill_candidate"].update(skill_id=learned_id, expected_version=1, correction_event_ids=["correction"])
        return finish_response(request, summary="7×8=56，计算已核对", **changes)

    runtime, requests = await work_runtime(respond)
    first = await create_job(runtime)
    await runtime.job_runner._run_job(first, "group:work")
    await asyncio.gather(*list(runtime.job_runner._learning_tasks.values()))
    learned = [item for item in await runtime.event_store.list_skills("group:work") if item["author"] == "agent"]
    assert len(learned) == 1
    learned_id = learned[0]["id"]
    assert learned[0]["version"] == 1 and learned[0]["source"]["job_id"] == first
    assert await runtime.event_store.read_skill(learned_id, "group:other") is None
    # Explicit publication covers this exact version, never earlier/future bodies.
    await runtime.event_store.publish_skill(learned_id, "group:work", 1)
    phase = 2
    second = await create_job(runtime, source_id="correction", proposal_id="second",
                              source_text="上一方法缺少复核，请增加逆运算校验，再核对7乘8")
    await runtime.job_runner._run_job(second, "group:work")
    await asyncio.gather(*list(runtime.job_runner._learning_tasks.values()))
    updated = await runtime.event_store.read_skill(learned_id, "group:work")
    assert updated["version"] == 2 and updated["scope"] == "group:work"
    assert (await runtime.event_store.get_job(second, "group:work"))["skill_versions"][learned_id] == 1
    assert (await runtime.event_store.read_skill(learned_id, "group:other"))["version"] == 1
    assert await runtime.event_store.read_skill(learned_id, "group:other", 2) is None
    phase = 3
    third = await create_job(runtime, source_id="again", proposal_id="third")
    await runtime.job_runner._run_job(third, "group:work")
    await asyncio.gather(*list(runtime.job_runner._learning_tasks.values()))
    assert (await runtime.event_store.get_job(third, "group:work"))["skill_versions"][learned_id] == 2
    calls = await runtime.event_store.list_model_calls("group:work")
    assert sum(item["purpose"] == "skill_maintenance" for item in calls) == 2
    assert all(item["status"] == "saved" for item in await runtime.event_store.list_skill_candidates("group:work"))


@pytest.mark.asyncio
async def test_skill_queue_drains_new_candidates_without_granting_time_or_losing_revision_cost(work_runtime, monkeypatch):
    from len_bot.cognition.jobs import JobResult, SkillCandidate
    from len_bot.skills import learning
    clock = [100.0]
    monkeypatch.setattr(learning, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    started, release = asyncio.Event(), asyncio.Event()

    async def respond(request, number):
        if number == 1:
            started.set()
            await release.wait()
        return completion(native_call("save_skill", {"name": "核算方法", "applicability": "给定算式",
            "steps": ["读取数值并计算"], "verification": ["核对运算结果"], "exclusions": []}))

    runtime, requests = await work_runtime(respond)

    async def ready(source_id, proposal_id, elapsed):
        job_id = await create_job(runtime, source_id=source_id, proposal_id=proposal_id)
        result, event = await runtime.event_store.save_tool_observation("group:work", "calculate", {"expression": "7*8"},
            ToolResult(content="56", coverage="arithmetic", evidence_kind="retrieval"), background_work=True)
        await runtime.commit_tool_observation(event)
        await runtime.event_store.job_checkpoint(job_id, "group:work", 1, result_ids=[result.result_id], elapsed_seconds=elapsed)
        await runtime.event_store.complete_job(job_id, "group:work", 1, JobResult(status="completed", summary="56", result_ids=[result.result_id]),
            skill_candidate=SkillCandidate(name="核算方法", lesson="保留确定输入和验证步骤", result_ids=[result.result_id]))
        return job_id

    first = await ready("source", "first", 25)
    runtime.job_runner._start_learning("group:work")
    await asyncio.wait_for(started.wait(), 3)
    exhausted = await ready("exhausted-source", "exhausted", runtime.config.job_max_seconds)
    latest = await ready("new-source", "new", 0)
    runtime.job_runner._start_learning("group:work")
    await control_job(runtime, first, "revise", ["新增条件"])
    clock[0] += 10
    release.set()
    await asyncio.gather(*list(runtime.job_runner._learning_tasks.values()))
    records = {record["job_id"]: record for record in await runtime.event_store.list_skill_candidates("group:work")}
    assert records[first]["status"] == "obsolete"
    assert records[exhausted]["status"] == "failed"
    assert records[latest]["status"] == "saved"
    assert (await runtime.event_store.get_job(first, "group:work"))["elapsed_seconds"] == 35
    assert (await runtime.event_store.get_job(exhausted, "group:work"))["model_steps"] == 0
    assert len(requests) == 2
