import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.models import EpisodeOutcome, TaskProposal
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.memory.models import MemoryProposal
from len_bot.memory.store import MemoryStore
from len_bot.memory.writes import validate_memory_proposal
from len_bot.runtime.metrics import RuntimeMetrics
from len_bot.scenes.reducer import SceneReducer
from len_bot.web.auth import create_session, revoke_session
from len_bot.web.query_service import RuntimeQueryService
from len_bot.web.routes.cockpit import router


SCENE, BOT = "group:panel", "user:999"


@pytest_asyncio.fixture
async def panel(tmp_path):
    store = EventStore(str(tmp_path / "panel.db"), clock=lambda: 1000)
    await store.initialize()
    memories = MemoryStore(store._db, store._write_lock, clock=store.clock)
    await memories.initialize()
    runtime = SimpleNamespace(event_store=store, memory_store=memories, config=RuntimeConfig(bot_qq=999),
        metrics=RuntimeMetrics(), shadow_mode=True, allowed_scenes={SCENE}, shadow_would_send_log=[], outcomes=[], operator_sources=[])
    runtime.provider_registry = SimpleNamespace(snapshot=lambda: {"providers": [], "routing": {
        "conversation": {"provider_id": "p", "model": "chat"}, "work": {"provider_id": "p", "model": "research"}, "maintenance": None}})
    runtime.query_service = RuntimeQueryService(runtime)
    sessions = {}

    async def record(event):
        session = SceneReducer.reduce(sessions.get(event.scene_id), event, BOT)
        await store.commit_scene_event(event, session.model_dump(mode="json"))
        sessions[event.scene_id] = session
        return event

    async def operator_event(scene_id, operation, operator, details):
        return await record(Event(event_type=EventType.OPERATOR_ACTION, scene_id=scene_id,
            actor_id=f"operator:{operator}", timestamp=1000,
            payload={**details, "operation": operation, "operator": operator}))

    async def submit(scene_id, outcome, *, source_event_ids=None):
        runtime.outcomes.append(outcome)
        runtime.operator_sources.append(source_event_ids)
        try:
            await store.commit_proposal_transaction("operator-test", scene_id, outcome.task_proposals,
                outcome.resolve_open_loop_ids, outcome.memory_proposals, job_proposals=outcome.job_proposals,
                origin_mode="shadow", bot_actor_id=BOT)
        except ValueError as error:
            return SimpleNamespace(accepted=False, reason=str(error))
        return SimpleNamespace(accepted=True, reason="accepted")

    runtime.record_operator_event, runtime.operator_outcome = operator_event, submit
    await record(Event(id="source", event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE, actor_id="user:A",
                       timestamp=900, payload={"raw_text": "请少开玩笑", "sender": {"nickname": "A"}}))
    await record(Event(id="delivered", event_type=EventType.MESSAGE_SENT, scene_id=SCENE, actor_id=BOT,
                       timestamp=901, payload={"content": "知道了", "message_id": "11"}))
    created = await store.commit_proposal_transaction("seed", SCENE, [], [], [MemoryProposal(scope=SCENE,
        subject="user:A", kind="preference", statement="A明确希望少开玩笑", basis="reported", evidence=["source"])], bot_actor_id=BOT)
    memory_id = created[2][0].id
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(router)
    token = create_session("admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        client.cookies.set("session_token", token)
        yield runtime, client, memory_id
    revoke_session(token)
    await store.close()


@pytest.mark.asyncio
async def test_panel_reads_committed_fact_sessions_and_single_ledger(panel):
    runtime, client, memory_id = panel
    await runtime.operator_outcome(SCENE, EpisodeOutcome(decision_reason="seed job", job_proposals=[
        JobProposal(proposal_id="job", goal="资料核对", source_event_ids=["source"])]))
    scenes = (await client.get("/api/cockpit/scenes")).json()["scenes"]
    assert scenes == [{"scene_id": SCENE, "version": 2, "participant_count": 1,
                       "last_event_at": 901, "last_bot_message_at": 901, "active_job_count": 1, "pending_wake_count": 0}]
    detail = (await client.get(f"/api/cockpit/scenes/{SCENE}")).json()
    assert set(detail) == {"session", "preferences", "jobs", "recent_messages", "recent_deliveries",
                           "history_batches", "history_status", "maintenance"}
    assert detail["preferences"][0]["id"] == memory_id
    assert detail["session"]["participants"]["user:A"]["nickname"] == "A"
    assert detail["recent_deliveries"][0]["id"] == "delivered"
    assert "social_world" not in json.dumps(detail)
    overview = await runtime.query_service.overview()
    assert overview["stats"]["conversation_model"] == "chat"
    assert overview["stats"]["work_model"] == "research"
    assert "normal_model" not in overview["stats"]
    items = (await client.get("/api/cockpit/memories", params={"scope": SCENE})).json()
    assert items[0]["statement"] == "A明确希望少开玩笑" and items[0]["basis"] == "reported"
    assert "certainty" not in items[0] and "key" not in items[0]
    assert (await client.get("/api/cockpit/scenes/group:missing")).status_code == 404


@pytest.mark.asyncio
async def test_authenticated_refutation_keeps_original_evidence_and_operator_reason(panel):
    runtime, client, memory_id = panel
    response = await client.post(f"/api/cockpit/memories/{memory_id}/refute", json={"reason": "  运营核对后认为理解过度  "})
    assert response.status_code == 200
    memory = await runtime.query_service.memory(memory_id)
    assert memory["status"] == "refuted" and memory["evidence"] == ["source"]
    assert memory["statement"] == "A明确希望少开玩笑" and memory["revision_reason"] == "运营核对后认为理解过度"
    assert len(memory["revision_evidence"]) == 1 and memory["revision_evidence"] != ["source"]
    evidence = (await runtime.query_service.query_events(event_type="OPERATOR_ACTION"))[0]
    assert evidence["actor_id"] == "operator:admin"
    assert evidence["payload"]["target_memory_id"] == memory_id
    assert evidence["payload"]["operator"] == "admin"
    assert runtime.outcomes[-1].memory_proposals[0].evidence == [evidence["id"]]
    assert (await client.get(f"/api/cockpit/memories/{memory_id}/chain")).json()["chain"][0] == memory
    repeated = await client.post(f"/api/cockpit/memories/{memory_id}/refute", json={"reason": "再次撤销"})
    assert repeated.status_code == 409


@pytest.mark.asyncio
async def test_memory_control_requires_authentication_and_explicit_reason(panel):
    runtime, client, memory_id = panel
    for payload in ({}, {"reason": "   "}):
        assert (await client.post(f"/api/cockpit/memories/{memory_id}/refute", json=payload)).status_code == 422
    client.cookies.clear()
    assert (await client.post(f"/api/cockpit/memories/{memory_id}/refute", json={"reason": "撤销"})).status_code == 401
    assert not await runtime.query_service.query_events(event_type="OPERATOR_ACTION")
    assert (await runtime.query_service.memory(memory_id))["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"actor_id": "user:A"}, {"operation": "other_operation"},
    {"target_memory_id": "different-memory"}, {"reason": "different reason"}, {"operator": "different-user"},
])
async def test_operator_evidence_exception_cannot_be_repurposed(panel, change):
    runtime, client, memory_id = panel
    payload = {"operation": "memory_refute", "target_memory_id": memory_id, "reason": "撤销", "operator": "admin"}
    payload.update({key: value for key, value in change.items() if key != "actor_id"})
    event = Event(event_type=EventType.OPERATOR_ACTION, scene_id=SCENE, actor_id=change.get("actor_id", "operator:admin"), payload=payload)
    await runtime.event_store.append_event(event)
    proposal = MemoryProposal(operation="refute", target_memory_ids=[memory_id], reason="撤销", evidence=[event.id])
    with pytest.raises(ValueError, match="Operator evidence"):
        await validate_memory_proposal(runtime.event_store._db, proposal, SCENE, bot_actor_id=BOT)
    assert (await runtime.query_service.memory(memory_id))["status"] == "active"


@pytest.mark.asyncio
async def test_operator_refute_event_cannot_create_new_beliefs(panel):
    runtime, client, memory_id = panel
    event = await runtime.record_operator_event(SCENE, "memory_refute", "admin", {"target_memory_id": memory_id, "reason": "撤销"})
    proposal = MemoryProposal(subject="user:A", kind="fact", statement="从管理员动作推断的新事实", evidence=[event.id])
    with pytest.raises(ValueError, match="independent memory evidence"):
        await validate_memory_proposal(runtime.event_store._db, proposal, SCENE, bot_actor_id=BOT)


@pytest.mark.asyncio
async def test_task_and_job_controls_use_operator_proposals_and_keep_version_checks(panel):
    runtime, client, _ = panel
    created = await runtime.event_store.commit_proposal_transaction("seed-task", SCENE,
        [TaskProposal(proposal_id="reminder", description="稍后提醒", due_at=1100, source_event_ids=["source"])], [], [],
        job_proposals=[JobProposal(proposal_id="work", goal="查询", source_event_ids=["source"])], bot_actor_id=BOT)
    task = next(item for item in created[0] if not item.id.startswith("job_"))
    job = (await runtime.query_service.jobs(SCENE))[0]
    assert (await client.post(f"/api/cockpit/tasks/{task.id}/trigger_now")).status_code == 200
    updated = await runtime.query_service.get_task(task.id)
    assert updated["due_at"] == 1001 and updated["status"] == "pending"
    assert runtime.outcomes[-1].task_proposals[0].source_event_ids
    assert (await client.post(f"/api/cockpit/tasks/{task.id}/cancel")).status_code == 200
    assert (await runtime.query_service.get_task(task.id))["status"] == "cancelled"
    await runtime.event_store.mark_task_status(job['id'], 'processing')
    await runtime.event_store.save_job_exchange(job['id'], SCENE, 1, [
        {'role': 'assistant', 'tool_calls': [{'id': 'opaque-call', 'type': 'function',
            'function': {'name': 'read_result', 'arguments': '{}'},
            'extra_content': {'provider_private': 'opaque-continuation-secret'}}]},
        {'role': 'tool', 'tool_call_id': 'opaque-call', 'content': '已读取'}], 1)
    public_jobs = await client.get('/api/cockpit/jobs', params={'scene_id': SCENE})
    assert public_jobs.json()[0]['checkpoint']['exchange_count'] == 1
    assert 'opaque-continuation-secret' not in public_jobs.text and 'checkpoint_data' not in public_jobs.text
    assert (await client.post(f"/api/cockpit/tasks/{job['id']}/cancel")).status_code == 409
    assert (await client.post(f"/api/cockpit/jobs/{job['id']}/cancel", json={"expected_revision": 3})).status_code == 409
    assert (await client.post(f"/api/cockpit/jobs/{job['id']}/cancel", json={"expected_revision": 1})).status_code == 200
    assert (await runtime.query_service.job(job["id"]))["status"] == "cancelled"
    assert (await client.post(f"/api/cockpit/tasks/{task.id}/promote")).status_code == 404


@pytest.mark.asyncio
async def test_loop_resolution_uses_same_operator_submission_path(panel):
    runtime, client, _ = panel
    await runtime.event_store.save_open_loop({"id": "loop", "scene_id": SCENE, "target_actor_id": "user:A",
        "intent": "等回复", "source_event_id": "delivered", "status": "active", "created_at": 901, "expires_at": 1200})
    assert (await client.post("/api/cockpit/loops/loop/resolve")).status_code == 200
    assert (await runtime.query_service.open_loop("loop"))["status"] == "resolved"
    assert runtime.outcomes[-1].resolve_open_loop_ids == ["loop"]
    assert runtime.operator_sources[-1]
