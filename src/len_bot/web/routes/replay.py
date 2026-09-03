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
    # One entry per policy run; empty list = single run with defaults
    overrides: list[dict] = []


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

    lab = ReplayLab(runtime.config)
    runs = []
    override_sets = req.overrides if req.overrides else [{}]
    for idx, overrides in enumerate(override_sets):
        rows = await lab.run(event_objs, overrides=overrides)
        runs.append({
            "policy": f"Policy {chr(65 + idx)}" if len(override_sets) > 1 else "Replay",
            "overrides": overrides,
            "rows": rows,
            "summary": {
                "messages": sum(1 for r in rows if r["disposition"] in ("wake", "observe", "track")),
                "wake": sum(1 for r in rows if r["disposition"] == "wake"),
                "action": sum(1 for r in rows if r.get("cognition") == "ACTION"),
            },
        })
    return {"scene_id": req.scene_id, "event_count": len(event_objs), "runs": runs}
