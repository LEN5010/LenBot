import asyncio
from delivery_support import allow_fake_delivery
import json
import uuid
from openai import AsyncOpenAI

import pytest
import httpx

from len_bot.actions.models import ActionItem, ActionType, DeliveryResult, DeliveryStatus
from len_bot.cognition.jobs import JobProposal, JobResult
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from runtime_support import configure_fixture_profile
from len_bot.testing.replay import drain
from len_bot.testing.turns import turn_result
from len_bot.cognition.providers import RouteResolution
from len_bot.plugins.base import BasePlugin
from len_bot.plugins.models import PluginManifest, PluginPermission
from len_bot.tools.results import ToolResult
from len_bot.web.app import create_app


async def quiet(session, events):
    return turn_result(reason="测试不发言")


async def setup_runtime(tmp_path, **config):
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "jobs.db"), bot_qq=999, **config), mock_turn_handler=quiet, clock=lambda: 1000)
    await rt.start()
    await configure_fixture_profile(rt)
    await allow_fake_delivery(rt, 'group:jobs')
    await rt.scheduler.stop()
    rt.job_runner.running = False
    source = Event(id="source", event_type=EventType.OPERATOR_ACTION, scene_id="group:jobs", actor_id="operator:test", timestamp=1000)
    await rt.commit_tool_observation(source)
    return rt


async def submit(rt, proposal, *, content=None, task_ref=None, job_id=None, job_revision=None, fulfils_task_id=None, origin_mode="live"):
    actor = await rt.scene_manager.get_or_create_actor("group:jobs")
    mailbox = EpisodeMailbox(f"unit-job:{uuid.uuid4().hex}", actor.scene_id, actor.session.version)
    mailbox.origin_mode = origin_mode
    assert actor.acquire_episode_lease(mailbox.episode_id, mailbox)
    result = turn_result(reason="测试工作提案", content=content, task_ref=task_ref, job_id=job_id,
                           job_revision=job_revision, fulfils_task_id=fulfils_task_id)
    result.job_proposals = [proposal] if proposal else []
    try:
        return await actor.commit_turn(result, actor.session.last_observed_event_rowid,
            ["source"], actor.session.knowledge_revision, mailbox, rt.runtime_gate)
    finally:
        actor.release_episode_lease(mailbox.episode_id)


def create(**kwargs):
    return JobProposal(proposal_id="work", goal="比较公开资料", source_event_ids=["source"], **kwargs)


@pytest.mark.asyncio
async def test_job_creation_and_ack_rollback_together(tmp_path):
    rt = await setup_runtime(tmp_path)
    try:
        await rt.event_store._db.execute("CREATE TRIGGER reject_job BEFORE INSERT ON agent_jobs BEGIN SELECT RAISE(ABORT,'failure'); END")
        decision = await submit(rt, create(), content="我来查", task_ref="work")
        assert not decision.accepted
        assert not await rt.event_store.scene_tasks("group:jobs")
        assert not await rt.event_store.list_jobs("group:jobs")
        assert rt.action_queue._queue.empty()
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_revision_keeps_shadow_origin_budget_and_rejects_old_result_and_send(tmp_path):
    rt = await setup_runtime(tmp_path)
    try:
        assert not rt.shadow_mode and not rt.is_scene_shadow("group:jobs")
        decision = await submit(rt, create(), origin_mode="shadow")
        assert decision.accepted
        job = (await rt.event_store.list_jobs("group:jobs"))[0]
        assert job["origin_mode"] == "shadow"
        await rt.scheduler.run_due(1000)
        await rt.scene_manager._actors["group:jobs"]._queue.join()
        event = await rt.event_store.job_checkpoint(job["id"], job["scene_id"], 1, model_steps=2, tool_calls=3, limits=(16, 24, 300))
        await rt.commit_tool_observation(event)
        change = JobProposal(operation="revise", job_id=job["id"], expected_revision=1,
            constraints_add=["只接受图形界面"], source_event_ids=["source"])
        assert (await submit(rt, change)).accepted
        revised = await rt.event_store.get_job(job["id"], job["scene_id"])
        assert (revised["revision"], revised["model_steps"], revised["tool_calls"]) == (2, 2, 3)
        assert revised["origin_mode"] == "shadow"
        assert await rt.event_store.complete_job(job["id"], job["scene_id"], 1, JobResult(status="completed", summary="旧结果")) is None
        ready = await rt.event_store.complete_job(job["id"], job["scene_id"], 2, JobResult(status="completed", summary="新结果"))
        assert ready
        assert await rt.event_store.complete_job(job["id"], job["scene_id"], 2, JobResult(status="completed", summary="重复")) is None
        cancel = JobProposal(operation="cancel", job_id=job["id"], expected_revision=2, source_event_ids=["source"])
        assert (await submit(rt, cancel)).accepted
        calls = []
        async def send(action):
            calls.append(action)
            return DeliveryResult(status=DeliveryStatus.SENT, transport="test")
        rt.action_queue.send_adapter = send
        rt.action_queue.enqueue(ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id=job["scene_id"],
            content="过时内容", job_id=job["id"], job_revision=2))
        await rt.action_queue._queue.join()
        assert calls == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_job_recovery_requires_explicit_resume_and_retains_budget(tmp_path):
    rt = await setup_runtime(tmp_path)
    await submit(rt, create())
    job = (await rt.event_store.list_jobs("group:jobs"))[0]
    await rt.scheduler.run_due(1000)
    await rt.scene_manager._actors["group:jobs"]._queue.join()
    await rt.commit_tool_observation(await rt.event_store.job_checkpoint(job["id"], job["scene_id"], 1, model_steps=2))
    await rt.stop()
    second = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "jobs.db"), bot_qq=999), mock_turn_handler=quiet, clock=lambda: 1000)
    await second.start()
    await second.scheduler.stop()
    second.job_runner.running = False
    try:
        await drain(second)
        recovered = await second.event_store.get_job(job["id"], job["scene_id"])
        assert recovered["status"] == "review_required" and recovered["model_steps"] == 2
        assert recovered["origin_mode"] == "live"
        await second.set_shadow_mode(True)
        assert (await submit(second, JobProposal(operation="resume", job_id=job["id"], expected_revision=1, source_event_ids=["source"]))).accepted
        await second.set_shadow_mode(False)
        resumed = await second.event_store.get_job(job["id"], job["scene_id"])
        assert resumed["model_steps"] == 2 and resumed["origin_mode"] == "shadow"
    finally:
        await second.stop()


class SlowDocumentPlugin(BasePlugin):
    def __init__(self):
        super().__init__(PluginManifest(id="slow_docs", name="测试资料", permissions=[PluginPermission.REGISTER_TOOL]))
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.calls = 0
    async def on_load(self, context):
        async def lookup(args):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return ToolResult(content="资料包含手机界面步骤", evidence_kind="external")
        context.register_tool("lookup_document", "读取测试资料", {"type": "object", "properties": {}}, lookup, read_only=True)


class WorkRegistry:
    def __init__(self):
        self.calls = []
        self.client = AsyncOpenAI(api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.respond)))
    def snapshot(self):
        return {"providers": [{"id": "test", "enabled": True}], "routing": {
            "conversation": {"provider_id": "test", "model": "work"},
            "work": {"provider_id": "test", "model": "work"}}}
    def resolve(self, role):
        return RouteResolution(provider_id="test", model="work", client=self.client, role=role)
    async def respond(self, request):
        kwargs = json.loads(request.content)
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            function = {"name": "lookup_document", "arguments": "{}"}
        else:
            content = kwargs["messages"][1]["content"]
            brief = json.loads(content if isinstance(content, str) else content[0]["text"])
            assert "只用手机" in brief["constraints"]
            assert brief["result_ids"]
            result = JobResult(status="completed", summary="按手机约束整理了资料", result_ids=brief["result_ids"])
            function = {"name": "finish_work", "arguments": result.model_dump_json()}
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "work-call", "type": "function", "function": function}]}}]})


@pytest.mark.asyncio
async def test_work_can_be_revised_while_social_core_keeps_responding(tmp_path):
    sent = []
    async def send(action):
        sent.append(action.content)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="test", message_id=str(len(sent)))
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "live.db"), bot_qq=999), send_adapter=send, clock=lambda: 1000)
    async def social(session, events):
        messages = [event.model_dump(mode="json") for event in events]
        jobs = await rt.event_store.list_jobs("group:work")
        if not jobs:
            result = turn_result(reason="需要独立查询", content="我先查一下", task_ref="lookup")
            result.job_proposals = [JobProposal(proposal_id="lookup", goal="整理公开资料", source_event_ids=["first"])]
            return result
        job = jobs[0]
        if "只用手机" in json.dumps(messages, ensure_ascii=False) and "只用手机" not in job["constraints"]:
            result = turn_result(reason="用户补充了限制", content="知道了，按手机能操作的方式查")
            result.job_proposals = [JobProposal(operation="revise", job_id=job["id"], expected_revision=job["revision"],
                constraints_add=["只用手机"], source_event_ids=["late"])]
            return result
        if job["status"] == "result_ready":
            return turn_result(reason="结果就绪", content=job["result"]["summary"], job_id=job["id"],
                job_revision=job["revision"], fulfils_task_id=job["id"])
        return turn_result(reason="继续等待资料")
    rt.mock_turn_handler = social
    await rt.start()
    await allow_fake_delivery(rt, 'group:work')
    registry, plugin = WorkRegistry(), SlowDocumentPlugin()
    rt.provider_registry = registry
    await rt.plugin_host.load_plugin(plugin)
    def user(eid, text):
        return Event(id=eid, event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:work", actor_id="user:1",
            timestamp=1000, payload={"raw_text": text, "at_bot": True})
    try:
        await rt.receive_event(user("first", "帮我查资料"))
        await asyncio.wait_for(plugin.started.wait(), 5)
        await rt.receive_event(user("late", "只用手机"))
        await rt.scene_manager._actors["group:work"]._queue.join()
        await asyncio.gather(*list(rt._conversation_tasks.values()))
        assert "知道了，按手机能操作的方式查" in sent or not rt.action_queue._queue.empty()
        plugin.release.set()
        await asyncio.wait_for(drain(rt), 5)
        job = (await rt.event_store.list_jobs("group:work"))[0]
        assert job["revision"] == 2 and job["status"] == "completed"
        assert plugin.calls == 1 and len(registry.calls) == 2
        assert sent[-1] == "按手机约束整理了资料"
    finally:
        plugin.release.set()
        await rt.stop()
        await registry.client.close()


@pytest.mark.asyncio
async def test_work_budget_counts_failed_model_attempt_and_scope(tmp_path):
    rt = await setup_runtime(tmp_path, job_max_steps=1)
    class FailingRegistry(WorkRegistry):
        async def respond(self, request):
            self.calls.append(json.loads(request.content))
            raise httpx.ReadTimeout("fixture offline")
    registry = FailingRegistry()
    rt.provider_registry = registry
    try:
        await submit(rt, create())
        job = (await rt.event_store.list_jobs("group:jobs"))[0]
        assert await rt.event_store.get_job(job["id"], "group:other") is None
        rt.job_runner.running = True
        await asyncio.wait_for(drain(rt), 5)
        final = await rt.event_store.get_job(job["id"], job["scene_id"])
        assert final["model_steps"] == 1 and len(registry.calls) == 1
        assert final["result"]["status"] == "failed"
        assert final["status"] == "result_ready"  # Not delivered or fulfilled.
    finally:
        await rt.stop()
        await registry.client.close()


@pytest.mark.asyncio
async def test_progress_is_evidence_linked_bounded_and_panel_control_versioned(tmp_path):
    rt = await setup_runtime(tmp_path)
    try:
        await submit(rt, create())
        job = (await rt.event_store.list_jobs("group:jobs"))[0]
        await rt.scheduler.run_due(1000)
        await rt.scene_manager._actors["group:jobs"]._queue.join()
        result, event = await rt.event_store.save_tool_observation("group:jobs", "test", {}, ToolResult(content="原始资料", evidence_kind="external"), background_work=True)
        await rt.commit_tool_observation(event)
        await rt.commit_tool_observation(await rt.event_store.job_checkpoint(job["id"], "group:jobs", 1, result_ids=[result.result_id]))
        progress = await rt.event_store.report_job_progress(job["id"], "group:jobs", 1, "发现两份资料的日期不同", [result.result_id])
        assert progress
        assert await rt.event_store.report_job_progress(job["id"], "group:jobs", 1, "重复进展", [result.result_id]) is None
        with pytest.raises(ValueError):
            await rt.event_store.report_job_progress(job["id"], "group:jobs", 1, "伪造资料", ["foreign"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(rt)), base_url="http://test") as client:
            await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
            assert len((await client.get("/api/cockpit/jobs")).json()) == 1
            response = await client.post(f"/api/cockpit/jobs/{job['id']}/revise", json={"expected_revision": 1, "goal": "只整理确定的事实"})
            assert response.status_code == 200 and response.json()["job"]["revision"] == 2
            stale = await client.post(f"/api/cockpit/jobs/{job['id']}/cancel", json={"expected_revision": 1})
            assert stale.status_code == 409
            cancelled = await client.post(f"/api/cockpit/jobs/{job['id']}/cancel", json={"expected_revision": 2})
            assert cancelled.status_code == 200 and cancelled.json()["job"]["status"] == "cancelled"
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_cancel_during_tool_stops_before_another_model_call(tmp_path):
    rt = await setup_runtime(tmp_path)
    plugin, registry = SlowDocumentPlugin(), WorkRegistry()
    rt.provider_registry = registry
    await rt.plugin_host.load_plugin(plugin)
    try:
        await submit(rt, create())
        job = (await rt.event_store.list_jobs("group:jobs"))[0]
        rt.job_runner.running = True
        await rt.scheduler.run_due(1000)
        await asyncio.wait_for(plugin.started.wait(), 5)
        assert (await submit(rt, JobProposal(operation="cancel", job_id=job["id"], expected_revision=1, source_event_ids=["source"]))).accepted
        plugin.release.set()
        await asyncio.wait_for(drain(rt), 5)
        cancelled = await rt.event_store.get_job(job["id"], job["scene_id"])
        assert cancelled["status"] == "cancelled" and cancelled["result"] is None
        assert len(registry.calls) == 1
    finally:
        plugin.release.set()
        await rt.stop()
        await registry.client.close()
