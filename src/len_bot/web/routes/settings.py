from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

class PersonaSettingsRequest(BaseModel):
    identity_name: str
    identity_persona: str
    bot_qq: int

@router.get("/persona")
async def get_persona_settings(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return {
        "identity_name": runtime.config.identity_name,
        "identity_persona": runtime.config.identity_persona,
        "bot_qq": runtime.config.bot_qq
    }

@router.post("/persona")
async def update_persona_settings(req: PersonaSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    runtime.config.identity_name = req.identity_name.strip()
    runtime.config.identity_persona = req.identity_persona.strip()
    runtime.config.bot_qq = req.bot_qq
    runtime.bot_actor_id = f"user:{req.bot_qq}"
    runtime.action_queue.bot_actor_id = runtime.bot_actor_id
    runtime.scene_manager.bot_actor_id = runtime.bot_actor_id
    for actor in runtime.scene_manager._actors.values():
        actor.bot_actor_id = runtime.bot_actor_id

    await runtime.event_store.save_dynamic_config("persona_config", {
        "identity_name": runtime.config.identity_name,
        "identity_persona": runtime.config.identity_persona,
        "bot_qq": runtime.config.bot_qq
    })
    return {"success": True, "message": "Persona settings saved"}
