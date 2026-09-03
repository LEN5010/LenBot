from fastapi import APIRouter, Request, Depends
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/websocket", tags=["websocket"])

@router.get("/status")
async def get_ws_status(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    adapter = getattr(runtime, "_onebot_adapter", None)

    connected = False
    remote_address = None
    if adapter and getattr(adapter, "_active_ws", None):
        connected = True
        try:
            remote_address = str(adapter._active_ws.remote_address)
        except Exception:
            remote_address = "connected"

    return {
        "host": runtime.config.ws_host,
        "port": runtime.config.ws_port,
        "connected": connected,
        "remote_address": remote_address,
        "server_status": "listening" if getattr(adapter, "_server", None) else "stopped",
        "echo_counter": getattr(adapter, "_echo_counter", 0) if adapter else 0
    }

@router.post("/disconnect")
async def disconnect_ws_client(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    adapter = getattr(runtime, "_onebot_adapter", None)
    if adapter and getattr(adapter, "_active_ws", None):
        ws = adapter._active_ws
        await ws.close(code=1000, reason="Disconnected from admin dashboard")
        return {"success": True, "message": "WebSocket client disconnected"}
    return {"success": False, "message": "No active client connected"}
