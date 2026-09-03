"""Provider & Routing management API (ADR-0020).

Replaces the legacy single BaseURL + two model names semantics. Providers are
multi-endpoint, hot-swappable, persisted in `provider_config`; API keys are
write-only and never echoed back.
"""

import time
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from len_bot.cognition.providers import ProviderConfig, RouteTarget, RoutingConfig
from len_bot.cognition.router import CognitiveTier
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/models", tags=["models"])


class ProviderUpsertRequest(BaseModel):
    id: str
    base_url: str
    api_style: str = "openai"
    api_key: Optional[str] = None  # omit on update to keep the existing key
    enabled: bool = True
    timeout_seconds: float = 60.0


class RoutingUpdateRequest(BaseModel):
    normal_provider_id: str
    normal_model: str
    deliberate_provider_id: str
    deliberate_model: str


async def _persist_and_apply(runtime, providers: list[ProviderConfig], routing: RoutingConfig) -> None:
    await runtime.provider_registry.apply_update(providers, routing)
    await runtime.event_store.save_dynamic_config("provider_config", runtime.provider_registry.export())


@router.get("/providers")
async def list_providers(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    return runtime.provider_registry.snapshot()


@router.post("/providers")
async def upsert_provider(req: ProviderUpsertRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    snap = runtime.provider_registry.export()
    providers = {p["id"]: ProviderConfig(**p) for p in snap.get("providers", [])}

    existing = providers.get(req.id)
    new_provider = ProviderConfig(
        id=req.id.strip(),
        api_style=req.api_style,
        base_url=req.base_url.strip(),
        api_key=(req.api_key.strip() if req.api_key else (existing.api_key if existing else "")),
        enabled=req.enabled,
        timeout_seconds=req.timeout_seconds,
    )
    providers[new_provider.id] = new_provider
    routing = RoutingConfig(**snap["routing"]) if snap.get("routing") else RoutingConfig(
        normal=RouteTarget(provider_id=new_provider.id, model=""),
        deliberate=RouteTarget(provider_id=new_provider.id, model=""),
    )
    try:
        await _persist_and_apply(runtime, list(providers.values()), routing)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": f"Provider '{new_provider.id}' saved (hot-applied)"}


@router.get("/routing")
async def get_routing(request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    snap = runtime.provider_registry.snapshot()
    return snap["routing"]


@router.post("/routing")
async def update_routing(req: RoutingUpdateRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    snap = runtime.provider_registry.export()
    providers = [ProviderConfig(**p) for p in snap.get("providers", [])]
    if not providers:
        raise HTTPException(status_code=400, detail="No providers configured yet")
    routing = RoutingConfig(
        normal=RouteTarget(provider_id=req.normal_provider_id, model=req.normal_model.strip()),
        deliberate=RouteTarget(provider_id=req.deliberate_provider_id, model=req.deliberate_model.strip()),
    )
    try:
        await _persist_and_apply(runtime, providers, routing)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": "Routing updated (hot-applied)"}


class ModelTestRequest(BaseModel):
    provider_id: str
    model: str


@router.post("/test")
async def test_model_connection(req: ModelTestRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    snap = runtime.provider_registry.export()
    provider = next((ProviderConfig(**p) for p in snap.get("providers", []) if p["id"] == req.provider_id), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider '{req.provider_id}' not found")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=provider.api_key or "missing", base_url=provider.base_url, timeout=30.0)
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
        return {"success": True, "latency_ms": latency_ms, "response": reply.strip(), "model": req.model}
    except Exception as e:
        latency_ms = int((time.time() - start_t) * 1000)
        return {"success": False, "latency_ms": latency_ms, "error": str(e)}


@router.get("/metrics")
async def get_routing_metrics(request: Request, user: str = Depends(get_current_user)):
    """ADR-0020 routing metrics: calls/errors/tokens/latency p50-p95/escalations."""
    runtime = request.app.state.runtime
    return runtime.metrics.snapshot()
