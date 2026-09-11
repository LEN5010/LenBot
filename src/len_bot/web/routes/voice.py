"""Voice exemplar management API (ADR-0038 §5).

Operator-authored examples, injected as stable few-shot style
context (never decision input). scene_id='' means the exemplar applies to all
scenes.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Optional
from len_bot.web.auth import get_current_user
from len_bot.media.models import MessageSegment, segment_text

router = APIRouter(prefix="/api/voice", tags=["voice"])


class VoiceExampleCreateRequest(BaseModel):
    content: str = ""
    segments: list[MessageSegment] | None = Field(default=None, max_length=20)
    context: str = Field(default="", max_length=8000)
    tag: str = Field(default="", max_length=200)
    scene_id: str = ""

    @model_validator(mode="after")
    def body(self):
        if self.segments is None:
            self.segments = [MessageSegment(type="text", text=self.content.strip())]
        if not self.segments or not segment_text(self.segments).strip():
            raise ValueError("样例需要文字或图片")
        self.content = segment_text(self.segments)
        return self


class VoiceExampleToggleRequest(BaseModel):
    example_id: str
    enabled: bool

class VoiceExampleFromMessageRequest(BaseModel):
    scene_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    context: str = Field(default="", max_length=8000)
    tag: str = Field(default="", max_length=200)


@router.get("/exemplars")
async def list_exemplars(scene_id: Optional[str] = None, request: Request = None, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return {"exemplars": await runtime.query_service.list_voice_examples(scene_id)}


@router.post("/exemplars")
async def create_exemplar(req: VoiceExampleCreateRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        example = await runtime.event_store.add_voice_example(
            scene_id=req.scene_id.strip(), content=req.content, context=req.context.strip(), tag=req.tag.strip(),
            segments=[part.model_dump(exclude_none=True) for part in req.segments])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"success": True, "exemplar": example}

@router.post("/exemplars/from-message")
async def create_exemplar_from_message(req: VoiceExampleFromMessageRequest, request: Request, user: str = Depends(get_current_user)):
    """Turn a confirmed real Bot delivery into an operator-edited exemplar."""
    runtime = request.app.state.runtime
    rows = await runtime.event_store.events_by_ids(req.scene_id, [req.event_id], 2**63 - 1)
    event = rows[0] if rows else None
    if event is None or event.event_type.value != "MESSAGE_SENT" or event.actor_id != runtime.bot_actor_id:
        raise HTTPException(400, "只能从本场景已真实发送的 Bot 消息创建样例")
    if event.metadata.get("simulated") or event.payload.get("delivery_status") != "sent" or event.payload.get("delivery_unknown"):
        raise HTTPException(400, "该消息没有确定的真实送达回执，不能作为表达样例")
    segments = event.payload.get("segments") or []
    if any(part.get("type") not in {"text", "image"} for part in segments):
        raise HTTPException(400, "表达样例只支持文字与运营图片")
    try:
        example = await runtime.event_store.add_voice_example(req.scene_id, content=event.payload.get("content", ""),
            context=req.context.strip(), tag=req.tag.strip(), segments=segments)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"success": True, "source_event_id": req.event_id, "exemplar": example}


@router.post("/exemplars/toggle")
async def toggle_exemplar(req: VoiceExampleToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        updated = await runtime.event_store.set_voice_example_enabled(req.example_id, req.enabled)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail="未找到该示例")
    return {"success": True}


@router.put("/exemplars/{example_id}")
async def update_exemplar(example_id: str, req: VoiceExampleCreateRequest, request: Request, user: str = Depends(get_current_user)):
    try:
        updated = await request.app.state.runtime.event_store.update_voice_example(
            example_id, scene_id=req.scene_id.strip(), content=req.content,
            context=req.context.strip(), tag=req.tag.strip(), segments=[part.model_dump(exclude_none=True) for part in req.segments])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
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
