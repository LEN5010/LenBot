import time
from fastapi import APIRouter, Request, Depends
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/overview", tags=["overview"])

@router.get("/stats")
async def get_overview_stats(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    stats = await runtime.event_store.get_stats()
    
    # Memory count
    mem_count = 0
    if runtime.memory_store:
        cursor = await runtime.memory_store._db.execute("SELECT COUNT(*) FROM memories WHERE status = 'active';")
        (mem_count,) = await cursor.fetchone()

    # Active scenes
    active_scenes_count = 0
    scenes_summary = []
    for scene_id, actor in runtime.scene_manager._actors.items():
        if actor.state:
            scenes_summary.append({
                "scene_id": scene_id,
                "version": actor.state.version,
                "activity": actor.state.activity_level,
                "active_topic": actor.state.active_topic or "None",
                "bot_engagement": actor.state.bot_engagement,
                "consecutive_bot_messages": actor.state.consecutive_bot_messages
            })
            if actor.state.activity_level in ("active", "hot"):
                active_scenes_count += 1

    # WebSocket connection
    ws_connected = False
    adapter = getattr(runtime, "_onebot_adapter", None)
    if adapter and getattr(adapter, "_active_ws", None) is not None:
        ws_connected = True

    # Speaking budget sample
    budget_threshold = runtime.attention_engine.speaking_budget.base_threshold

    return {
        "stats": {
            **stats,
            "active_scenes": active_scenes_count,
            "memory_beliefs_count": mem_count,
            "websocket_connected": ws_connected,
            "speaking_budget_threshold": budget_threshold,
            "normal_model": runtime.config.default_model,
            "deliberate_model": runtime.config.deliberate_model,
            "identity_name": runtime.config.identity_name,
            "bot_qq": runtime.config.bot_qq,
            "uptime_seconds": time.time() - getattr(runtime, "_started_at", time.time())
        },
        "scenes": scenes_summary
    }

@router.get("/recent_events")
async def get_recent_events(request: Request, limit: int = 25, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    cursor = await runtime.event_store._db.execute("""
        SELECT id, event_type, scene_id, actor_id, timestamp, payload
        FROM events
        ORDER BY timestamp DESC
        LIMIT ?;
    """, (limit,))
    rows = await cursor.fetchall()
    import json
    return [
        {
            "id": r[0],
            "event_type": r[1],
            "scene_id": r[2],
            "actor_id": r[3],
            "timestamp": r[4],
            "payload": json.loads(r[5]) if r[5] else {}
        }
        for r in rows
    ]
