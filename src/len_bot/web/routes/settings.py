from fastapi import APIRouter, Request, Depends
from typing import Optional

from pydantic import BaseModel
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

class PersonaSettingsRequest(BaseModel):
    identity_name: str
    identity_persona: str
    identity_core: Optional[str] = None
    conversation_style: Optional[str] = None
    bot_qq: Optional[int] = None

@router.get("/persona")
async def get_persona_settings(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return {
        "identity_name": runtime.config.identity_name,
        "identity_core": runtime.config.identity_core,
        "identity_persona": runtime.config.identity_persona,
        "conversation_style": runtime.config.conversation_style,
        "bot_qq": runtime.config.bot_qq
    }

@router.post("/persona")
async def update_persona_settings(req: PersonaSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    runtime.config.identity_name = req.identity_name.strip()
    runtime.config.identity_persona = req.identity_persona.strip()
    if req.identity_core is not None:
        runtime.config.identity_core = req.identity_core.strip()
    if req.conversation_style is not None:
        runtime.config.conversation_style = req.conversation_style.strip()
    if req.bot_qq is not None:
        runtime.update_bot_identity(req.bot_qq)

    await runtime.event_store.save_dynamic_config("persona_config", {
        "identity_name": runtime.config.identity_name,
        "identity_core": runtime.config.identity_core,
        "identity_persona": runtime.config.identity_persona,
        "conversation_style": runtime.config.conversation_style,
        "bot_qq": runtime.config.bot_qq
    })
    return {"success": True, "message": "人格与说话风格已保存，并立即生效"}
