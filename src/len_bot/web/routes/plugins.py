from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# In-memory mock plugin registry (Reserved Architecture §104)
RESERVED_PLUGINS = [
    {
        "id": "bilibili_live_sensor",
        "name": "Bilibili 直播监控感官",
        "description": "持续监控指定主播的开播、切片与下播状态，向事件总线投递 LIVE_STARTED 事实事件。",
        "version": "0.1.0-alpha",
        "author": "LenBot Core Team",
        "enabled": False,
        "status": "idle",
        "config": {
            "room_ids": [123456],
            "check_interval_seconds": 60
        }
    },
    {
        "id": "web_search_tool",
        "name": "实时联网认知检索插件",
        "description": "赋予 Pi Agent 在 Deliberate 认知阶层进行实时网页搜索与信息提取的 Agentic 工具。",
        "version": "0.2.0-beta",
        "author": "LenBot Core Team",
        "enabled": True,
        "status": "active",
        "config": {
            "search_engine": "duckduckgo",
            "max_results": 5
        }
    },
    {
        "id": "tieba_hot_watcher",
        "name": "百度贴吧热榜观察哨",
        "description": "周期性观察指定贴吧热门讨论，向 InterestModel 注入当前热度话题与流行梗。",
        "version": "0.1.0-stub",
        "author": "LenBot Core Team",
        "enabled": False,
        "status": "disabled",
        "config": {
            "bar_names": ["孙笑川吧", "抗压背锅吧"],
            "scan_window_hours": 4
        }
    },
    {
        "id": "github_release_sensor",
        "name": "开源仓库 Release 监控",
        "description": "监控核心开源项目（如 vLLM / Ollama）新版本发布，并生成主动话题探讨。",
        "version": "0.1.2",
        "author": "Community",
        "enabled": False,
        "status": "idle",
        "config": {
            "repos": ["vllm-project/vllm"],
            "poll_interval_minutes": 30
        }
    }
]

class PluginToggleRequest(BaseModel):
    plugin_id: str
    enabled: bool

class PluginConfigRequest(BaseModel):
    plugin_id: str
    config: dict

@router.get("/list")
async def list_plugins(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    saved = await runtime.event_store.get_dynamic_config("plugins_state")
    if saved:
        # Merge saved states
        for p in RESERVED_PLUGINS:
            if p["id"] in saved:
                p["enabled"] = saved[p["id"]].get("enabled", p["enabled"])
                p["config"] = saved[p["id"]].get("config", p["config"])
                p["status"] = "active" if p["enabled"] else "disabled"
    return RESERVED_PLUGINS

@router.post("/toggle")
async def toggle_plugin(req: PluginToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    found = None
    for p in RESERVED_PLUGINS:
        if p["id"] == req.plugin_id:
            p["enabled"] = req.enabled
            p["status"] = "active" if req.enabled else "disabled"
            found = p
            break

    if not found:
        raise HTTPException(status_code=404, detail="Plugin not found")

    # Persist state
    state = {p["id"]: {"enabled": p["enabled"], "config": p["config"]} for p in RESERVED_PLUGINS}
    await runtime.event_store.save_dynamic_config("plugins_state", state)

    return {"success": True, "plugin_id": req.plugin_id, "enabled": req.enabled}

@router.post("/config")
async def save_plugin_config(req: PluginConfigRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    found = None
    for p in RESERVED_PLUGINS:
        if p["id"] == req.plugin_id:
            p["config"].update(req.config)
            found = p
            break

    if not found:
        raise HTTPException(status_code=404, detail="Plugin not found")

    state = {p["id"]: {"enabled": p["enabled"], "config": p["config"]} for p in RESERVED_PLUGINS}
    await runtime.event_store.save_dynamic_config("plugins_state", state)

    return {"success": True, "plugin_id": req.plugin_id, "config": found["config"]}
