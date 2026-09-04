"""Replay Lab API (ADR-0022): deterministic offline replay of a recorded window."""

from typing import Optional
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from len_bot.testing.replay import ReplayLab
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/replay", tags=["replay"])


class ReplayRequest(BaseModel):
    scene_id: str
    since: Optional[float] = None
    until: Optional[float] = None
    limit: int = 300


@router.post("")
async def replay_scene(req: ReplayRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    events = await runtime.event_store.query_timeline(
        scene_id=req.scene_id,
        start_time=req.since or 0.0,
        end_time=req.until or 1e18,
        allowed_scopes=[req.scene_id],
        limit=req.limit,
    )
    # query_timeline returns dicts; convert back to Event for the pure reducer
    from len_bot.events.models import Event, EventType
    event_objs = [
        Event(
            id=e["id"],
            event_type=EventType(e["event_type"]),
            scene_id=e["scene_id"],
            actor_id=e["actor_id"],
            timestamp=e["timestamp"],
            payload=e["payload"],
        )
        for e in events
    ]

    lab = ReplayLab(runtime.config, runtime.social_core)
    rows = await lab.run(event_objs)
    runs = [{
        "policy": "Social Core",
        "rows": rows,
        "summary": {
            "cognition": len(rows),
            "silence": sum(1 for r in rows if r["decision"] == "silence"),
            "would_speak": sum(1 for r in rows if r["decision"] == "speak"),
        },
    }]
    return {"scene_id": req.scene_id, "event_count": len(event_objs), "runs": runs}
