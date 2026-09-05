import time
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from len_bot.web.auth import get_current_user
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryStatus
from len_bot.cognition.models import TaskProposal, EpisodeOutcome, FinalDisposition
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.jobs import JobProposal

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


class EventInjectionRequest(BaseModel):
    actor_id: str = "user:admin"
    raw_text: str
    event_type: str = "GROUP_MESSAGE_RECEIVED"


class MemoryActionRequest(BaseModel):
    reason: Optional[str] = None


class ShadowToggleRequest(BaseModel):
    enabled: bool


def _service(request: Request):
    return request.app.state.runtime.query_service


@router.get("/tool-results")
async def tool_results(scene_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _service(request).tool_results(scene_id)


@router.get("/tool-results/{result_id}")
async def tool_result(result_id: str, scene_id: str, request: Request, offset: int = 0, user: str = Depends(get_current_user)):
    if offset < 0:
        raise HTTPException(400, "offset must be nonnegative")
    result = await _service(request).tool_result(scene_id, result_id, offset)
    if result is None:
        raise HTTPException(404, "未找到本场景的资料")
    return result


@router.get("/scenes")
async def list_scenes(request: Request, user: str = Depends(get_current_user)):
    scenes = await _service(request).list_scenes()
    return {"scenes": scenes}


@router.get("/scenes/{scene_id}")
async def get_scene_detail(scene_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _service(request).scene_detail(scene_id)


@router.post("/scenes/{scene_id}/inject")
async def inject_scene_event(
    scene_id: str,
    req: EventInjectionRequest,
    request: Request,
    user: str = Depends(get_current_user)
):
    """Manually injects an event into SceneActor and EventBus for manual testing / intervention."""
    runtime = request.app.state.runtime
    try:
        etype = EventType(req.event_type)
    except ValueError:
        etype = EventType.GROUP_MESSAGE_RECEIVED

    now = time.time()
    event = Event(
        event_type=etype,
        scene_id=scene_id,
        actor_id=req.actor_id,
        timestamp=now,
        payload={"raw_text": req.raw_text}
    )
    await runtime.receive_event(event)
    return {"success": True, "event_id": event.id, "scene_id": scene_id}


@router.get("/tasks")
async def list_tasks(request: Request, status: Optional[str] = None, user: str = Depends(get_current_user)):
    return await _service(request).list_tasks(status=status)


@router.get("/jobs")
async def jobs(request: Request, scene_id: str | None = None, user: str = Depends(get_current_user)):
    return await _service(request).jobs(scene_id)


class JobControlRequest(BaseModel):
    expected_revision: int
    goal: str | None = None
    constraints_add: list[str] = []
    constraints_remove: list[str] = []


@router.post("/jobs/{job_id}/{operation}")
async def control_job(job_id: str, operation: str, req: JobControlRequest, request: Request, user: str = Depends(get_current_user)):
    if operation not in {"cancel", "revise", "resume"}:
        raise HTTPException(400, "未知工作操作")
    runtime = request.app.state.runtime
    job = await _service(request).job(job_id)
    if not job:
        raise HTTPException(404, "工作不存在")
    event = Event(event_type=EventType.OPERATOR_ACTION, scene_id=job["scene_id"], actor_id=f"operator:{user}",
        payload={"operation": operation, "job_id": job_id})
    await runtime.commit_tool_observation(event)
    actor = await runtime.scene_manager.get_or_create_actor(job["scene_id"])
    mailbox = EpisodeMailbox(f"operator:{event.id}", job["scene_id"], actor.state.version)
    proposal = JobProposal(operation=operation, job_id=job_id, expected_revision=req.expected_revision,
        goal=req.goal, constraints_add=req.constraints_add, constraints_remove=req.constraints_remove, source_event_ids=[event.id])
    decision = await actor.submit_proposal(mailbox.episode_id, EpisodeOutcome(disposition=FinalDisposition.SILENCE,
        decision_reason="运营修改信息工作", job_proposals=[proposal]), mailbox, runtime.runtime_gate)
    if not decision.accepted:
        raise HTTPException(409, decision.reason)
    return {"success": True, "job": await _service(request).job(job_id)}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _edit_task(request, task_id, TaskProposal(operation="cancel", task_id=task_id))


class TaskUpdateRequest(BaseModel):
    due_at: float
    description: str = ""


async def _edit_task(request, task_id, proposal):
    runtime = request.app.state.runtime
    task = await _service(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="未找到这个任务")
    actor = await runtime.scene_manager.get_or_create_actor(task["scene_id"])
    mailbox = EpisodeMailbox(f"operator:{task_id}", task["scene_id"], actor.state.version)
    decision = await actor.submit_proposal(mailbox.episode_id, EpisodeOutcome(
        disposition=FinalDisposition.SILENCE, decision_reason="管理员修改任务",
        task_proposals=[proposal]), mailbox, runtime.runtime_gate)
    if not decision.accepted:
        raise HTTPException(status_code=409, detail="任务未修改：" + decision.reason)
    return {"success": True, "task_id": task_id}


@router.post("/tasks/{task_id}/update")
async def update_task(task_id: str, req: TaskUpdateRequest, request: Request, user: str = Depends(get_current_user)):
    return await _edit_task(request, task_id, TaskProposal(operation="update", task_id=task_id,
                                                         due_at=req.due_at, description=req.description))


@router.post("/tasks/{task_id}/trigger_now")
async def trigger_task_now(task_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    success = await runtime.scheduler.trigger_task_now(task_id)
    return {"success": success, "task_id": task_id}


@router.post("/tasks/{task_id}/promote")
async def promote_task(task_id: str, request: Request, user: str = Depends(get_current_user)):
    """ADR-0029, §23.4: Promote a task to a re-armed template or global template."""
    runtime = request.app.state.runtime
    promoted = await runtime.scheduler.promote_task(task_id)
    if not promoted:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {"success": True, "task": promoted}


@router.get("/loops")
async def list_loops(request: Request, status: Optional[str] = None, user: str = Depends(get_current_user)):
    return await _service(request).list_open_loops(status=status)


@router.post("/loops/{loop_id}/resolve")
async def resolve_loop(loop_id: str, request: Request, user: str = Depends(get_current_user)):
    """Authority path (ADR-0022): resolution goes through OpenLoopManager — the same
    code path the runtime itself uses — never a direct SQL UPDATE from the UI."""
    runtime = request.app.state.runtime
    loop = next((l for l in await _service(request).list_open_loops() if l["id"] == loop_id), None)
    if not loop:
        raise HTTPException(status_code=404, detail="Open loop not found")
    resolved = await runtime.open_loop_manager.resolve_loop(loop_id, loop["scene_id"])
    return {"success": resolved, "loop_id": loop_id}


@router.get("/memories")
async def list_memories(
    request: Request,
    status: Optional[str] = None,
    scope: Optional[str] = None,
    subject: Optional[str] = None,
    user: str = Depends(get_current_user)
):
    return await _service(request).list_memories(status=status, scope=scope, subject=subject)


@router.get("/memories/{memory_id}/chain")
async def memory_chain(memory_id: str, request: Request, user: str = Depends(get_current_user)):
    chain = await _service(request).memory_chain(memory_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"chain": chain}


@router.post("/memories/{memory_id}/refute")
async def refute_memory(memory_id: str, req: MemoryActionRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await runtime.memory_store.update_memory_status(memory_id, MemoryStatus.REFUTED)
    return {"success": True, "memory_id": memory_id, "status": "refuted"}


@router.post("/memories/{memory_id}/supersede")
async def supersede_memory(memory_id: str, req: MemoryActionRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await runtime.memory_store.update_memory_status(memory_id, MemoryStatus.SUPERSEDED)
    return {"success": True, "memory_id": memory_id, "status": "superseded"}


@router.post("/memories/{memory_id}/promote")
async def promote_memory(memory_id: str, request: Request, user: str = Depends(get_current_user)):
    """Human Operator Promotion (ADR-0024, §3.3): Creates a new record with scope='global-safe'.
    The original memory is untouched. Cognition and reflection have zero authority to promote.
    """
    runtime = request.app.state.runtime
    promoted = await runtime.memory_store.promote_memory(memory_id)
    if not promoted:
        raise HTTPException(status_code=404, detail="Memory not found or not active")
    return {"success": True, "original_id": memory_id, "promoted_id": promoted.id, "scope": promoted.scope}


@router.get("/events")
async def query_events(
    request: Request,
    scene_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    limit: int = 80,
    user: str = Depends(get_current_user)
):
    return await _service(request).query_events(
        scene_id=scene_id, actor_id=actor_id, event_type=event_type,
        since=since, until=until, limit=limit,
    )


@router.get("/traces")
async def list_traces(
    request: Request,
    scene_id: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    user: str = Depends(get_current_user)
):
    return await _service(request).query_traces(scene_id=scene_id, kind=kind, limit=limit)


@router.get("/shadow")
async def shadow_log(request: Request, limit: int = 100, user: str = Depends(get_current_user)):
    service = _service(request)
    return {
        "enabled": request.app.state.runtime.shadow_mode,
        "would_send": service.shadow_would_send(limit=limit)
    }


@router.post("/shadow/toggle")
async def shadow_toggle(req: ShadowToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await runtime.set_shadow_mode(req.enabled)
    return {"success": True, "shadow_mode": runtime.shadow_mode}


class ShadowAnnotationRequest(BaseModel):
    id: Optional[str] = None
    stimulus_id: Optional[str] = None
    scene_id: str
    label: str  # TP, FP, TN, FN
    comment: Optional[str] = None


@router.post("/shadow-annotations")
async def create_shadow_annotation(
    req: ShadowAnnotationRequest,
    request: Request,
    user: str = Depends(get_current_user)
):
    runtime = request.app.state.runtime
    label = req.label.upper().strip()
    if label not in ("TP", "FP", "TN", "FN"):
        raise HTTPException(status_code=400, detail="Label must be one of: TP, FP, TN, FN")
    res = await runtime.event_store.save_shadow_annotation({
        "id": req.id,
        "stimulus_id": req.stimulus_id,
        "scene_id": req.scene_id,
        "label": label,
        "comment": req.comment
    })
    return {"success": True, "annotation": res}


@router.get("/shadow-annotations")
async def list_shadow_annotations(
    request: Request,
    scene_id: Optional[str] = None,
    limit: int = 100,
    user: str = Depends(get_current_user)
):
    service = _service(request)
    items = await service.list_shadow_annotations(scene_id=scene_id, limit=limit)
    stats = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for item in items:
        lbl = item.get("label")
        if lbl in stats:
            stats[lbl] += 1
    total = sum(stats.values())
    accuracy = (stats["TP"] + stats["TN"]) / total if total > 0 else 0.0
    precision = stats["TP"] / (stats["TP"] + stats["FP"]) if (stats["TP"] + stats["FP"]) > 0 else 0.0
    return {
        "annotations": items,
        "stats": stats,
        "total": total,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4)
    }
