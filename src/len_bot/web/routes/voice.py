"""Voice exemplar management API (ADR-0038 §5).

Operator-authored examples, injected as stable few-shot style
context (never decision input). scene_id='' means the exemplar applies to all
scenes.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice"])


class VoiceExampleCreateRequest(BaseModel):
    content: str
    context: str = ""
    tag: str = ""
    scene_id: str = ""


class VoiceExampleToggleRequest(BaseModel):
    example_id: str
    enabled: bool


@router.get("/exemplars")
async def list_exemplars(scene_id: Optional[str] = None, request: Request = None, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return {"exemplars": await runtime.query_service.list_voice_examples(scene_id)}


@router.post("/exemplars")
async def create_exemplar(req: VoiceExampleCreateRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")
    example = await runtime.event_store.add_voice_example(
        scene_id=req.scene_id.strip(),
        content=content,
        context=req.context.strip(),
        tag=req.tag.strip(),
    )
    return {"success": True, "exemplar": example}


@router.post("/exemplars/toggle")
async def toggle_exemplar(req: VoiceExampleToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    updated = await runtime.event_store.set_voice_example_enabled(req.example_id, req.enabled)
    if not updated:
        raise HTTPException(status_code=404, detail="未找到该示例")
    return {"success": True}


@router.put("/exemplars/{example_id}")
async def update_exemplar(example_id: str, req: VoiceExampleCreateRequest, request: Request, user: str = Depends(get_current_user)):
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    updated = await request.app.state.runtime.event_store.update_voice_example(
        example_id, scene_id=req.scene_id.strip(), content=req.content.strip(),
        context=req.context.strip(), tag=req.tag.strip())
    if not updated:
        raise HTTPException(status_code=404, detail="未找到该示例")
    return {"success": True}


@router.delete("/exemplars/{example_id}")
async def delete_exemplar(example_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    deleted = await runtime.event_store.delete_voice_example(example_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该示例")
    return {"success": True}
