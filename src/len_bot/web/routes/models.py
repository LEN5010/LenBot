import time
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from openai import AsyncOpenAI
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/models", tags=["models"])

class ModelConfigRequest(BaseModel):
    openai_base_url: str
    openai_api_key: Optional[str] = None
    default_model: str
    deliberate_model: str

class ModelTestRequest(BaseModel):
    openai_base_url: str
    openai_api_key: Optional[str] = None
    model: str

@router.get("/config")
async def get_model_config(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    key = runtime.config.openai_api_key
    masked_key = ""
    if key:
        masked_key = key[:3] + "..." + key[-4:] if len(key) > 8 else "***"

    return {
        "openai_base_url": runtime.config.openai_base_url,
        "openai_api_key_masked": masked_key,
        "has_api_key": bool(key),
        "default_model": runtime.config.default_model,
        "deliberate_model": runtime.config.deliberate_model,
    }

@router.post("/config")
async def update_model_config(req: ModelConfigRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    
    runtime.config.openai_base_url = req.openai_base_url.strip()
    if req.openai_api_key:
        runtime.config.openai_api_key = req.openai_api_key.strip()
    runtime.config.default_model = req.default_model.strip()
    runtime.config.deliberate_model = req.deliberate_model.strip()

    # Re-initialize Pi client with updated parameters
    if runtime.config.openai_api_key:
        runtime.pi_core._client = AsyncOpenAI(
            api_key=runtime.config.openai_api_key,
            base_url=runtime.config.openai_base_url
        )

    # Persist in dynamic config
    await runtime.event_store.save_dynamic_config("model_config", {
        "openai_base_url": runtime.config.openai_base_url,
        "default_model": runtime.config.default_model,
        "deliberate_model": runtime.config.deliberate_model,
    })

    return {"success": True, "message": "Model configuration updated successfully"}

@router.post("/test")
async def test_model_connection(req: ModelTestRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    api_key = req.openai_api_key.strip() if req.openai_api_key else runtime.config.openai_api_key
    base_url = req.openai_base_url.strip() or runtime.config.openai_base_url

    if not api_key:
        raise HTTPException(status_code=400, detail="No API key provided or configured")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    start_t = time.time()
    try:
        completion = await client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "Ping test: respond with 'pong'"}],
            max_tokens=10,
            temperature=0.1
        )
        latency_ms = int((time.time() - start_t) * 1000)
        reply = completion.choices[0].message.content or ""
        return {
            "success": True,
            "latency_ms": latency_ms,
            "response": reply.strip(),
            "model": req.model
        }
    except Exception as e:
        latency_ms = int((time.time() - start_t) * 1000)
        return {
            "success": False,
            "latency_ms": latency_ms,
            "error": str(e)
        }
