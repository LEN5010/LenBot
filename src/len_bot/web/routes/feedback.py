from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from len_bot.events.models import Event, EventType
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.get("")
async def feedback(request: Request, scene_id: str, user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.reply_feedback(scene_id)


class FeedbackLabel(BaseModel):
    scene_id: str
    sent_event_id: str
    human_event_id: str | None = None
    kind: Literal["naturalness", "followup", "correction", "positive", "negative", "unrelated", "unknown"]
    acceptable: bool | None = None
    comment: str = Field(default="", max_length=4000)


@router.post("")
async def label_feedback(req: FeedbackLabel, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    event = Event(event_type=EventType.REPLY_FEEDBACK_LABELLED, scene_id=req.scene_id,
        actor_id=f"operator:{user}", payload=req.model_dump(), timestamp=runtime.clock())
    try:
        await runtime.commit_tool_observation(event)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(400, str(error)) from error
    return {"event_id": event.id}
