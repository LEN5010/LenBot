"""Manage discovered plugins through the root configuration.

RuntimeQueryService distinguishes configured entries from loaded plugins;
save responses retain the actual restart requirement and source status.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, ValidationError
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
    return runtime.query_service.plugins()


@router.post("/toggle")
async def toggle_plugin(req: PluginToggleRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    if not any(item["id"] == req.plugin_id for item in runtime.query_service.plugins()):
        raise HTTPException(status_code=404, detail="Plugin not found")
    try:
        await runtime.update_plugin_settings(req.plugin_id, enabled=req.enabled)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败：" + str(error.strerror)) from error
    except Exception as error:
        raise HTTPException(409, '插件状态更新未完成，请核对保存值与运行错误：' + str(error)) from error
    return {"success": True, "plugin_id": req.plugin_id, "enabled": req.enabled,
            "requires_restart": runtime.restart_required}



@router.post("/config")
async def save_plugin_config(req: PluginConfigRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        # Omitted fields keep their existing values, including credentials.
        await runtime.update_plugin_settings(req.plugin_id, values=req.config)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plugin not found")
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败：" + str(error.strerror)) from error
    except Exception as error:
        raise HTTPException(409, '配置更新未完成，请核对保存值与运行错误：' + str(error)) from error
    public=next(item for item in runtime.query_service.plugins() if item["id"]==req.plugin_id)
    return {"success":True,"plugin_id":req.plugin_id,"config":public["config"],
            "secret_fields":public["secret_fields"],"config_set":public["config_set"],
            "requires_restart":runtime.restart_required, 'state': public['state'], 'config_apply': public['config_apply']}
