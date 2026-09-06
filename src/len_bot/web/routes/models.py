"""Operator-owned model profiles and an isolated native capability check."""

import base64
import io
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.providers import ModelProfile, ProviderConfig, ProviderRegistry, RouteResolution, RoutingConfig
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/models", tags=["models"])


class ProviderUpsertRequest(BaseModel):
    id: str
    base_url: str
    api_style: str = "openai"
    api_key: str | None = None
    enabled: bool = True
    timeout_seconds: float = Field(default=60.0, gt=0)
    models: list[str] | None = None

    @field_validator("id", "base_url")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("供应商名称和接口地址不能为空")
        return value.strip()


async def _persist_and_apply(runtime, providers: list[ProviderConfig], routing: RoutingConfig | None) -> None:
    async with runtime.config_update_lock:
        candidate = ProviderRegistry()
        await candidate.apply_update(providers, routing)
        await runtime.event_store.save_dynamic_config("provider_config", candidate.export())
        await runtime.provider_registry.apply_update(providers, routing)


def _configuration(runtime):
    snapshot = runtime.provider_registry.export()
    providers = [ProviderConfig.model_validate(item) for item in snapshot.get("providers", [])]
    routing = RoutingConfig.model_validate(snapshot["routing"]) if snapshot.get("routing") else None
    return providers, routing


def _public_error(error, api_key=""):
    message = str(error)
    if api_key:
        message = message.replace(api_key, "[已隐藏密钥]")
    return f"{type(error).__name__}: {message}"[:500]


@router.get("/providers")
async def list_providers(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.providers()


@router.post("/providers")
async def upsert_provider(req: ProviderUpsertRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    existing_providers, routing = _configuration(runtime)
    providers = {provider.id: provider for provider in existing_providers}
    existing = providers.get(req.id)
    providers[req.id] = ProviderConfig(id=req.id, base_url=req.base_url, api_style=req.api_style,
        api_key=req.api_key.strip() if req.api_key and req.api_key.strip() else (existing.api_key if existing else ""),
        enabled=req.enabled, timeout_seconds=req.timeout_seconds,
        models=sorted({model.strip() for model in req.models if model.strip()}) if req.models is not None else (existing.models if existing else []))
    try:
        await _persist_and_apply(runtime, list(providers.values()), routing)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"success": True, "message": f"供应商“{req.id}”已保存"}


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    providers, routing = _configuration(runtime)
    if not any(provider.id == provider_id for provider in providers):
        raise HTTPException(404, "供应商不存在")
    if routing and any(profile.provider_id == provider_id for profile in (routing.conversation, routing.work)):
        raise HTTPException(409, "请先将对话和工作路由改到其他供应商")
    await _persist_and_apply(runtime, [provider for provider in providers if provider.id != provider_id], routing)
    return {"success": True, "message": "供应商已删除"}


@router.get("/providers/{provider_id}/models")
async def fetch_provider_models(provider_id: str, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    try:
        models = await runtime.query_service.provider_models(provider_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except Exception as error:
        raise HTTPException(502, f"获取模型失败：{type(error).__name__}") from error
    return {"provider_id": provider_id, "models": models}


class ProviderModelsUpdateRequest(BaseModel):
    models: list[str]


@router.post("/providers/{provider_id}/models")
async def save_provider_models(provider_id: str, req: ProviderModelsUpdateRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    providers, routing = _configuration(runtime)
    provider = next((item for item in providers if item.id == provider_id), None)
    if provider is None:
        raise HTTPException(404, "供应商不存在")
    selected = {model.strip() for model in req.models if model.strip()}
    active = {profile.model for profile in (routing.conversation, routing.work) if profile.provider_id == provider_id} if routing else set()
    provider.models = sorted(selected | active)
    await _persist_and_apply(runtime, providers, routing)
    message = "可选模型已保存" + ("；当前路由使用的模型已保留" if active - selected else "")
    return {"success": True, "message": message, "models": provider.models}


@router.get("/routing")
async def get_routing(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.providers()["routing"]


@router.post("/routing")
async def update_routing(req: RoutingConfig, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    providers, _ = _configuration(runtime)
    if not providers:
        raise HTTPException(400, "请先添加模型供应商")
    try:
        await _persist_and_apply(runtime, providers, req)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"success": True, "message": "对话与工作模型已保存，从下一次运行开始生效"}


def _probe_tool(name, properties):
    return {"type": "function", "function": {"name": name, "description": "提交本次隔离能力检查的观察结果",
        "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}}


def _probe_arguments(response, expected_tool):
    if response.finish_reason not in {"stop", "tool_calls"} or len(response.tool_calls) != 1 or response.tool_calls[0].name != expected_tool:
        raise ValueError(f"模型没有完整调用指定工具 {expected_tool}")
    arguments = json.loads(response.tool_calls[0].arguments)
    if not isinstance(arguments, dict):
        raise ValueError("工具参数不是对象")
    return arguments


@router.post("/test")
async def test_model_connection(req: ModelProfile, request: Request, user: str = Depends(get_current_user)):
    """Two model calls, synthetic pixels and a local receipt; no scene or send access."""
    providers, _ = _configuration(request.app.state.runtime)
    provider = next((item for item in providers if item.id == req.provider_id), None)
    if provider is None:
        raise HTTPException(404, "供应商不存在")
    if not provider.enabled:
        raise HTTPException(400, "请先启用这个供应商")
    client = AsyncOpenAI(api_key=provider.api_key or "missing", base_url=provider.base_url,
        timeout=provider.timeout_seconds, max_retries=0)
    gateway = ModelGateway(RouteResolution(provider_id=provider.id, model=req.model, client=client,
        reasoning_effort=req.reasoning_effort), max_output_tokens=4096)
    started = time.monotonic()
    checks = {"image_reading": False, "forced_tool": False, "tool_continuation": False}
    try:
        number, token = secrets.randbelow(90) + 10, secrets.token_hex(8)
        picture = Image.new("RGB", (240, 120), "white")
        ImageDraw.Draw(picture).text((35, 15), str(number), fill="black", font=ImageFont.load_default(size=72))
        image_bytes = io.BytesIO()
        picture.save(image_bytes, format="PNG")
        tools = [_probe_tool("record_image_number", {"number": {"type": "integer"}}),
            _probe_tool("finish_probe", {"number": {"type": "integer"}, "verification_token": {"type": "string"}})]
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "读出图片中的两位数字，通过record_image_number提交。随后读取工具回执，将原数字和verification_token原样交给finish_probe。"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+base64.b64encode(image_bytes.getvalue()).decode(), "detail": "high"}}]}]
        first = await gateway.complete(messages, tools, {"type": "function", "function": {"name": "record_image_number"}})
        arguments = _probe_arguments(first, "record_image_number")
        checks["forced_tool"] = True
        if type(arguments.get("number")) is not int or arguments["number"] != number:
            raise ValueError("模型未正确识别测试图片中的数字")
        checks["image_reading"] = True
        messages.extend([first.continuation, {"role": "tool", "tool_call_id": first.tool_calls[0].id,
            "content": json.dumps({"verification_token": token})}])
        second = await gateway.complete(messages, tools, {"type": "function", "function": {"name": "finish_probe"}})
        final = _probe_arguments(second, "finish_probe")
        if type(final.get("number")) is not int or final["number"] != number or final.get("verification_token") != token:
            raise ValueError("模型没有正确续接图片信息与工具回执")
        checks["tool_continuation"] = True
        return {"success": True, "model": req.model, "test": "native_multimodal_tools", "checks": checks,
            "latency_ms": round((time.monotonic()-started)*1000)}
    except Exception as error:
        return {"success": False, "model": req.model, "test": "native_multimodal_tools", "checks": checks,
            "latency_ms": round((time.monotonic()-started)*1000), "error": _public_error(error, provider.api_key)}
    finally:
        await client.close()


@router.get("/metrics")
async def get_routing_metrics(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.metrics()
