from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Optional

from pydantic import BaseModel, Field
from len_bot.config import AddressName
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.post("/reset")
async def reset_conversation_data(request: Request, user: str = Depends(get_current_user)):
    result = await request.app.state.runtime.reset_conversation_data(user)
    request.app.state.log_ring.clear()
    return result

class PersonaPresetRequest(BaseModel):
    preview_token: str


@router.get("/persona/diana")
async def preview_diana(request: Request, user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.preview_diana_persona()


@router.post("/persona/diana")
async def apply_diana(req: PersonaPresetRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        applied = await runtime.event_store.apply_diana_persona(runtime.config.bot_qq, req.preview_token)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if applied:
        from len_bot.cognition.diana import PERSONA
        saved = await runtime.event_store.get_dynamic_config("persona_config")
        for key in PERSONA:
            setattr(runtime.config, key, saved[key])
    return {"success": True, "applied": applied,
            "message": "新版嘉然人格与6组图文表达示例已应用，人工修改已保留" if applied else "已应用过新版嘉然人格，保留你的后续编辑"}

class PersonaSettingsRequest(BaseModel):
    character_context: Optional[str] = None
    identity_name: str
    identity_persona: str
    identity_core: Optional[str] = None
    conversation_style: Optional[str] = None
    address_names: list[AddressName]|None = Field(default=None,max_length=32)
    bot_qq: Optional[int] = None

@router.get("/persona")
async def get_persona_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.persona_settings()

@router.post("/persona")
async def update_persona_settings(req: PersonaSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    values = {
        key: getattr(runtime.config, key) for key in
        ("character_context", "identity_name", "identity_core", "identity_persona",
         "conversation_style", "address_names", "bot_qq")
    }
    values.update({key: value.strip() if isinstance(value, str) else value
                   for key, value in req.model_dump(exclude_none=True).items()})
    await runtime.event_store.save_dynamic_config("persona_config", values)
    for key, value in values.items():
        if key == "bot_qq":
            runtime.update_bot_identity(value)
        else:
            setattr(runtime.config, key, value)
    return {"success": True, "message": "人格与说话风格已保存，并立即生效"}
