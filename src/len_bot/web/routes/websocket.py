from typing import Literal, Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/websocket", tags=["websocket"])

@router.get("/status")
async def get_ws_status(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return runtime.query_service.onebot_status()


class OneBotConfigRequest(BaseModel):
    connection_mode: Literal["reverse_ws", "forward_ws"]
    action_transport: Literal["websocket", "http"]
    ws_url: str = Field(min_length=1)
    http_url: str = ""
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    access_token: Optional[str] = None

    @model_validator(mode="after")
    def validate_selected_endpoints(self):
        if self.connection_mode == "forward_ws" and not self.ws_url.strip():
            raise ValueError("主动连接时必须填写消息连接地址")
        if self.action_transport == "http" and not self.http_url.strip():
            raise ValueError("使用 HTTP 发送时必须填写接口地址")
        return self


@router.post("/config")
async def update_onebot_config(
    req: OneBotConfigRequest,
    request: Request,
    user: str = Depends(get_current_user),
):
    runtime = request.app.state.runtime
    config = runtime.config
    config.onebot_connection_mode = req.connection_mode
    config.onebot_action_transport = req.action_transport
    config.onebot_ws_url = req.ws_url.strip()
    config.onebot_http_url = req.http_url.strip()
    config.ws_host = req.host.strip()
    config.ws_port = req.port
    if req.access_token:
        config.onebot_access_token = req.access_token.strip()

    saved = {
        "onebot_connection_mode": config.onebot_connection_mode,
        "onebot_action_transport": config.onebot_action_transport,
        "onebot_ws_url": config.onebot_ws_url,
        "onebot_http_url": config.onebot_http_url,
        "onebot_access_token": config.onebot_access_token,
        "ws_host": config.ws_host,
        "ws_port": config.ws_port,
    }
    await runtime.event_store.save_dynamic_config("onebot_config", saved)

    adapter = getattr(runtime, "_onebot_adapter", None)
    if adapter:
        try:
            await adapter.restart()
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"连接配置已保存，但重新连接失败：{error}")
    return {"success": True, "message": "QQ 连接设置已保存，正在重新连接"}


@router.post("/test-http")
async def test_onebot_http(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    adapter = getattr(runtime, "_onebot_adapter", None)
    if not adapter:
        raise HTTPException(status_code=503, detail="OneBot 连接器尚未启动")
    result = await adapter.test_http_connection()
    if not result["success"]:
        wording = result.get("wording") or f"返回码 {result.get('retcode')}"
        raise HTTPException(status_code=502, detail=f"HTTP 接口测试失败：{wording}")
    return {"success": True, "message": f"HTTP 接口连接正常，耗时 {result['latency_ms']} 毫秒"}

@router.post("/disconnect")
async def disconnect_ws_client(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    adapter = getattr(runtime, "_onebot_adapter", None)
    if adapter and getattr(adapter, "_active_ws", None):
        ws = adapter._active_ws
        await ws.close(code=1000, reason="管理员主动断开")
        return {"success": True, "message": "连接已断开"}
    return {"success": False, "message": "当前没有已连接的客户端"}
