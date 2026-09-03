from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

class PersonaSettingsRequest(BaseModel):
    identity_name: str
    identity_persona: str
    bot_qq: int

class SocialSettingsRequest(BaseModel):
    monitored_keywords: list[str]
    bot_cooldown_seconds: int
    speaking_budget_base_threshold: float
    interest_topics: dict[str, float]

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

    await runtime.event_store.save_dynamic_config("persona_config", {
        "identity_name": runtime.config.identity_name,
        "identity_persona": runtime.config.identity_persona,
        "bot_qq": runtime.config.bot_qq
    })
    return {"success": True, "message": "Persona settings saved"}

@router.get("/social")
async def get_social_settings(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    interests = runtime.attention_engine.interest_model.topics
    threshold = runtime.attention_engine.speaking_budget.base_threshold

    return {
        "monitored_keywords": runtime.config.monitored_keywords,
        "bot_cooldown_seconds": runtime.config.bot_cooldown_seconds,
        "speaking_budget_base_threshold": threshold,
        "interest_topics": interests
    }

@router.post("/social")
async def update_social_settings(req: SocialSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    runtime.config.monitored_keywords = [k.strip() for k in req.monitored_keywords if k.strip()]
    runtime.config.bot_cooldown_seconds = req.bot_cooldown_seconds
    
    # Update speaking budget & interest model live in memory
    runtime.attention_engine.speaking_budget.base_threshold = max(0.1, min(1.0, req.speaking_budget_base_threshold))
    runtime.attention_engine.interest_model.topics = {
        k: max(0.0, min(1.0, float(v))) for k, v in req.interest_topics.items()
    }

    await runtime.event_store.save_dynamic_config("social_config", {
        "monitored_keywords": runtime.config.monitored_keywords,
        "bot_cooldown_seconds": runtime.config.bot_cooldown_seconds,
        "speaking_budget_base_threshold": runtime.attention_engine.speaking_budget.base_threshold,
        "interest_topics": runtime.attention_engine.interest_model.topics
    })

    return {"success": True, "message": "Social and attention settings saved"}
