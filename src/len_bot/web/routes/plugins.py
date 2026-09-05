"""Plugin management API (ADR-0021).

Serves ONLY the real PluginHost registry — the mock RESERVED_PLUGINS list is
deleted. Config forms are driven by each manifest's config_schema; enable state
and config persist in the `plugins_state` dynamic config and reload at start.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginToggleRequest(BaseModel):
    plugin_id: str
    enabled: bool


class PluginConfigRequest(BaseModel):
    plugin_id: str
    config: dict


@router.get("/list")
async def list_plugins(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return runtime.plugin_host.status_snapshot()


@router.post("/toggle")
async def toggle_plugin(req: PluginToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    if not runtime.plugin_host.has_plugin(req.plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    if req.enabled:
        await runtime.plugin_host.enable_plugin(req.plugin_id)
    else:
        await runtime.plugin_host.disable_plugin(req.plugin_id)
    await runtime.save_plugin_state()
    return {"success": True, "plugin_id": req.plugin_id, "enabled": req.enabled}


@router.post("/config")
async def save_plugin_config(req: PluginConfigRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        # Merge onto defaults so a partial form submit keeps unspecified defaults
        config = runtime.plugin_host.set_plugin_config(req.plugin_id, req.config)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plugin not found")
    await runtime.save_plugin_state()
    return {"success": True, "plugin_id": req.plugin_id, "config": config}
