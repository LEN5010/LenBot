from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from len_bot.web.auth import get_current_user
from len_bot.memory.models import MemoryProposal
from len_bot.cognition.models import TaskProposal, EpisodeOutcome, FinalDisposition
from len_bot.cognition.jobs import JobProposal

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


class MemoryActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000)


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
    detail = await _service(request).scene_detail(scene_id)
    if detail is None:
        raise HTTPException(404, "场景不存在")
    return detail


@router.get("/tasks")
async def list_tasks(request: Request, status: Optional[str] = None, user: str = Depends(get_current_user)):
    return await _service(request).list_tasks(status=status)


@router.get("/jobs")
async def jobs(request: Request, scene_id: str | None = None, user: str = Depends(get_current_user)):
    return await _service(request).jobs(scene_id)


class JobControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    goal: str | None = None
    constraints_add: list[str] = Field(default_factory=list)
    constraints_remove: list[str] = Field(default_factory=list)


@router.post("/jobs/{job_id}/{operation}")
async def control_job(job_id: str, operation: str, req: JobControlRequest, request: Request, user: str = Depends(get_current_user)):
    if operation not in {"cancel", "revise", "resume"}:
        raise HTTPException(400, "未知工作操作")
    runtime = request.app.state.runtime
    job = await _service(request).job(job_id)
    if not job:
        raise HTTPException(404, "工作不存在")
    event = await runtime.record_operator_event(job["scene_id"], f"job_{operation}", user,
        {"job_id": job_id, **req.model_dump()})
    proposal = JobProposal(operation=operation, job_id=job_id, expected_revision=req.expected_revision,
        goal=req.goal, constraints_add=req.constraints_add, constraints_remove=req.constraints_remove, source_event_ids=[event.id])
    decision = await runtime.operator_outcome(job["scene_id"], EpisodeOutcome(disposition=FinalDisposition.SILENCE,
        decision_reason="运营修改信息工作", job_proposals=[proposal]), source_event_ids=[event.id])
    if not decision.accepted:
        raise HTTPException(409, decision.reason)
    return {"success": True, "job": await _service(request).job(job_id)}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _edit_task(request, task_id, TaskProposal(operation="cancel", task_id=task_id), user)


class TaskUpdateRequest(BaseModel):
    due_at: float = Field(allow_inf_nan=False)
    description: str = ""


async def _edit_task(request, task_id, proposal, operator, *, trigger_now=False):
    runtime = request.app.state.runtime
    task = await _service(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="未找到这个任务")
    if task["payload"].get("kind") == "agent_job":
        raise HTTPException(409, "信息工作需要通过带版本号的工作控制接口修改")
    if trigger_now and task["status"] != "pending":
        raise HTTPException(409, "只有待执行任务可以立即触发")
    event = await runtime.record_operator_event(task["scene_id"], "task_trigger_now" if trigger_now else f"task_{proposal.operation}", operator,
        {"task_id": task_id, "changes": proposal.model_dump(exclude_none=True, exclude={"source_event_ids", "origin_mode"})})
    proposal.source_event_ids = [event.id]
    if trigger_now:
        proposal.due_at = _service(request).current_time() + 1.0
    decision = await runtime.operator_outcome(task["scene_id"], EpisodeOutcome(
        disposition=FinalDisposition.SILENCE, decision_reason="管理员修改任务",
        task_proposals=[proposal]), source_event_ids=[event.id])
    if not decision.accepted:
        raise HTTPException(status_code=409, detail="任务未修改：" + decision.reason)
    return {"success": True, "task_id": task_id}


@router.post("/tasks/{task_id}/update")
async def update_task(task_id: str, req: TaskUpdateRequest, request: Request, user: str = Depends(get_current_user)):
    return await _edit_task(request, task_id, TaskProposal(operation="update", task_id=task_id,
                                                         due_at=req.due_at, description=req.description), user)


@router.post("/tasks/{task_id}/trigger_now")
async def trigger_task_now(task_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _edit_task(request, task_id, TaskProposal(operation="update", task_id=task_id), user, trigger_now=True)


@router.get("/loops")
async def list_loops(request: Request, status: Optional[str] = None, user: str = Depends(get_current_user)):
    return await _service(request).list_open_loops(status=status)


@router.post("/loops/{loop_id}/resolve")
async def resolve_loop(loop_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    loop = await _service(request).open_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Open loop not found")
    event = await runtime.record_operator_event(loop["scene_id"], "loop_resolve", user, {"loop_id": loop_id})
    decision = await runtime.operator_outcome(loop["scene_id"], EpisodeOutcome(
        disposition=FinalDisposition.SILENCE, decision_reason="运营关闭待回应事项", resolve_open_loop_ids=[loop_id]), source_event_ids=[event.id])
    if not decision.accepted:
        raise HTTPException(409, decision.reason)
    return {"success": True, "loop_id": loop_id}


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
    memory = await _service(request).memory(memory_id)
    if memory is None:
        raise HTTPException(404, "认识不存在")
    event = await runtime.record_operator_event(memory["scope"], "memory_refute", user,
        {"target_memory_id": memory_id, "reason": req.reason})
    proposal = MemoryProposal(operation="refute", scope=memory["scope"], evidence=[event.id],
                              target_memory_ids=[memory_id], reason=req.reason)
    decision = await runtime.operator_outcome(memory["scope"], EpisodeOutcome(
        disposition=FinalDisposition.SILENCE, decision_reason="运营撤销认识", memory_proposals=[proposal]), source_event_ids=[event.id])
    if not decision.accepted:
        raise HTTPException(409, decision.reason)
    return {"success": True, "memory_id": memory_id, "status": "refuted"}


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
        **service.delivery_settings(),
        "would_send": service.shadow_would_send(limit=limit)
    }


@router.post("/shadow/toggle")
async def shadow_toggle(req: ShadowToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await runtime.set_shadow_mode(req.enabled)
    return {"success": True, "shadow_mode": runtime.shadow_mode}


class DeliveryScenesRequest(BaseModel):
    scene_ids: list[str]


@router.post("/shadow/scenes")
async def delivery_scenes(req: DeliveryScenesRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        await runtime.set_delivery_scenes(req.scene_ids)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"allowed_scenes": _service(request).delivery_settings()["allowed_scenes"]}


@router.get("/skills")
async def list_skills(request: Request, scene_id: str | None = None, user: str = Depends(get_current_user)):
    return await _service(request).skills(scene_id)


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, scene_id: str, request: Request, version: int | None = None, user: str = Depends(get_current_user)):
    result = await _service(request).skill(skill_id, scene_id, version)
    if result is None:
        raise HTTPException(404, "未找到本场景可读取的技能版本")
    return result


@router.get("/history-batches")
async def list_history_batches(scene_id: str, request: Request, user: str = Depends(get_current_user)):
    return await _service(request).history_batches(scene_id)


class SkillPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)


@router.post("/skills/{skill_id}/publish")
async def publish_skill(skill_id: str, req: SkillPublishRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    skill = await _service(request).skill(skill_id, req.scene_id, req.expected_version)
    if skill is None or skill["scene_id"] != req.scene_id:
        raise HTTPException(404, "未找到本场景的技能版本")
    await runtime.record_operator_event(req.scene_id, "skill_publish", user,
        {"skill_id": skill_id, "expected_version": req.expected_version})
    try:
        await runtime.event_store.publish_skill(skill_id, req.scene_id, req.expected_version)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"success": True, "skill": await _service(request).skill(skill_id, req.scene_id)}


class HistoryRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str = Field(min_length=1)


@router.post("/history-batches/{batch_id}/retry")
async def retry_history(batch_id: str, req: HistoryRetryRequest, request: Request, user: str = Depends(get_current_user)):
    batch = await _service(request).history_batch(batch_id)
    if batch is None or batch["scene_id"] != req.scene_id:
        raise HTTPException(404, "未找到本场景的历史区间")
    runtime = request.app.state.runtime
    await runtime.record_operator_event(req.scene_id, "history_retry", user, {"batch_id": batch_id})
    try:
        await runtime.retry_history(batch_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"success": True, "message": "已请求重新处理此原始区间，请刷新查看结果"}
