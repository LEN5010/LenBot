"""Operator-owned model profiles and an isolated native capability check."""

import base64
import io
import json
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.providers import ModelProfile, ProviderConfig, ProviderRegistry, RouteResolution, RoutingConfig, RetrievalRouting
from len_bot.web.auth import get_current_user
from len_bot.config_store import RootConfig
from len_bot.config_edit import ConfigEdit, ConfigEditConflict, merge_edit

router = APIRouter(prefix="/api/models", tags=["models"])


class ProviderUpsertRequest(BaseModel):
    id: str
    base_url: str
    api_style: str = "openai"
    api_key: str | None = None
    api_key_action: Literal['keep', 'replace', 'clear'] = 'keep'
    enabled: bool
    timeout_seconds: float = Field(gt=0)
    models: list[str]

    @field_validator("id", "base_url")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("供应商名称和接口地址不能为空")
        return value.strip()


def _public_provider(provider):
    data = dict(provider)
    data['api_key_masked'] = '已设置' if data.pop('api_key', '') else ''
    return data


def _merge_bindings(current, baseline, desired, path):
    # Parameters and operator confirmations belong to a provider/model pair.
    # Field-wise merging must not transfer edits to a concurrently retargeted
    # binding, or retain concurrent parameters when replacing the pair.
    if all(isinstance(value, dict) for value in (current, baseline, desired)):
        for role, next_binding in desired.items():
            original, saved = baseline.get(role), current.get(role)
            if original == next_binding or not all(isinstance(value, dict) for value in (original, saved, next_binding)):
                continue
            if not {'provider_id', 'model'} <= original.keys():
                raise HTTPException(422, '模型绑定基线缺少供应商或模型身份')
            original_pair = (original['provider_id'], original['model'])
            saved_pair = (saved['provider_id'], saved['model'])
            desired_pair = (next_binding['provider_id'], next_binding['model'])
            if saved_pair != original_pair or (desired_pair != original_pair and saved != original):
                raise ConfigEditConflict((*path, role))
    return merge_edit(current, baseline, desired, path)


async def _edit_models(runtime, mutate):
    async with runtime.config_update_lock:
        data = runtime.config_store.current.model_dump()
        mutate(data['models'])
        try:
            candidate = runtime.config_store.parse(data)
            runtime.config_store.save(candidate)
        except ConfigEditConflict:
            raise
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(500, '模型配置保存失败') from error
        try:
            await runtime.provider_registry.apply_update(candidate.models.providers, candidate.models.routing)
            runtime.retrieval_profiles = candidate.models.retrieval
            if runtime.retrieval_models:
                runtime.retrieval_models.profile = candidate.models.retrieval.embedding
            if runtime.memory_index:
                runtime.memory_index.profile = candidate.models.retrieval.embedding
        except Exception as error:
            raise HTTPException(409, {'message': '模型配置已保存，但运行应用失败：' + type(error).__name__,
                                      'config_saved': True}) from error
        return candidate.models


def _configuration(runtime):
    snapshot = runtime.query_service.model_configuration()
    providers = [ProviderConfig.model_validate(item) for item in snapshot.get("providers", [])]
    routing = RoutingConfig.model_validate(snapshot["routing"]) if snapshot.get("routing") else None
    return providers, routing, RetrievalRouting.model_validate(snapshot.get("retrieval") or {})


def _public_error(error, api_key=""):
    message = str(error)
    if api_key:
        message = message.replace(api_key, "[已隐藏密钥]")
    return f"{type(error).__name__}: {message}"[:500]


@router.get("/providers")
async def list_providers(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.model_settings_draft()


@router.post("/providers")
async def upsert_provider(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    req = ProviderUpsertRequest.model_validate(edit.values)
    if edit.baseline is not None and (not isinstance(edit.baseline, dict) or edit.baseline.get('id') != req.id):
        raise HTTPException(422, '供应商基线必须属于同一名称；改名请明确添加新供应商')

    def mutate(models):
        providers = {item['id']: item for item in models['providers']}
        existing = providers.get(req.id)
        if existing is None and edit.baseline is not None:
            raise ConfigEditConflict(('models', 'providers', req.id))
        public = _public_provider(existing) if existing else None
        baseline = dict(edit.baseline) if edit.baseline is not None else None
        # Credential revisions belong to the explicit secret operation, not
        # the editable fields. Keeping a key retains its current revision.
        if public is not None:
            public.pop('credential_revision', None)
        if baseline is not None:
            baseline.pop('credential_revision', None)
        desired = req.model_dump(exclude={'api_key', 'api_key_action'})
        desired['models'] = sorted({model.strip() for model in req.models if model.strip()})
        desired['api_key_masked'] = edit.baseline.get('api_key_masked', '') if isinstance(edit.baseline, dict) else ''
        merged = merge_edit(public, baseline, desired, ('models', 'providers', req.id))
        merged.pop('api_key_masked', None)
        key = existing['api_key'] if existing else ''
        revision = int((existing or {}).get('credential_revision') or 1)
        if req.api_key_action != 'keep':
            expected = int((edit.baseline or {}).get('credential_revision') or 1) if existing else 1
            if existing and revision != expected:
                raise ConfigEditConflict(('models', 'providers', req.id, 'api_key'))
            if req.api_key_action == 'replace' and not (req.api_key or '').strip():
                raise HTTPException(422, '替换密钥时必须填写新值')
            replacement = req.api_key.strip() if req.api_key_action == 'replace' else ''
            if replacement != key:
                revision += 1
            key = replacement
        merged.pop('api_key', None)
        providers[req.id] = {**merged, 'api_key': key, 'credential_revision': revision}
        models['providers'] = list(providers.values())

    await _edit_models(request.app.state.runtime, mutate)
    return {'success': True, 'message': f'供应商“{req.id}”已保存'}


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    def mutate(models):
        existing = next((item for item in models['providers'] if item['id'] == provider_id), None)
        if existing is None:
            raise HTTPException(404, '供应商不存在')
        if edit.values is not None:
            raise HTTPException(422, '删除供应商的目标值必须为 null')
        merge_edit(_public_provider(existing), edit.baseline, None, ('models', 'providers', provider_id))
        # RootConfig validates all role and retrieval references before save.
        models['providers'] = [item for item in models['providers'] if item['id'] != provider_id]
    await _edit_models(request.app.state.runtime, mutate)
    return {'success': True, 'message': '供应商已删除'}


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
async def save_provider_models(provider_id: str, edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    req = ProviderModelsUpdateRequest.model_validate(edit.values)
    def mutate(models):
        provider = next((item for item in models['providers'] if item['id'] == provider_id), None)
        if provider is None:
            raise HTTPException(404, '供应商不存在')
        selected = {model.strip() for model in req.models if model.strip()}
        chosen = merge_edit(provider['models'], edit.baseline, sorted(selected),
                            ('models', 'providers', provider_id, 'models'))
        profiles = [*(models['routing'] or {}).values(), *models['retrieval'].values()]
        active = {profile['model'] for profile in profiles if profile and profile['provider_id'] == provider_id}
        provider['models'] = sorted(set(chosen) | active)
    saved = await _edit_models(request.app.state.runtime, mutate)
    provider = next(item for item in saved.providers if item.id == provider_id)
    return {'success': True, 'message': '可选模型已保存；当前路由引用的模型保持在目录中', 'models': provider.models}


@router.get("/routing")
async def get_routing(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.providers()["routing"]


@router.post("/routing")
async def update_routing(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    desired = RoutingConfig.model_validate(edit.values).model_dump()
    def mutate(models):
        models['routing'] = _merge_bindings(models['routing'], edit.baseline, desired, ('models', 'routing'))
    await _edit_models(request.app.state.runtime, mutate)
    return {'success': True, 'message': '模型职责已保存，从下一次运行开始生效'}


@router.get("/retrieval")
async def get_retrieval(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.providers().get('retrieval', {'embedding': None, 'rerank': None})


@router.post("/retrieval")
async def update_retrieval(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    desired = RetrievalRouting.model_validate(edit.values).model_dump()
    def mutate(models):
        models['retrieval'] = _merge_bindings(models['retrieval'], edit.baseline, desired, ('models', 'retrieval'))
    await _edit_models(request.app.state.runtime, mutate)
    return {'success': True, 'message': '语义检索模型配置已保存；未绑定时不会发起检索请求'}


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
    providers, _, _ = _configuration(request.app.state.runtime)
    provider = next((item for item in providers if item.id == req.provider_id), None)
    if provider is None:
        raise HTTPException(404, "供应商不存在")
    if not provider.enabled:
        raise HTTPException(400, "请先启用这个供应商")
    if not provider.api_key.strip():
        raise HTTPException(400, "供应商尚未配置密钥")
    client = AsyncOpenAI(api_key=provider.api_key, base_url=provider.base_url,
        timeout=provider.timeout_seconds, max_retries=0, http_client=DefaultAsyncHttpxClient(trust_env=False))
    gateway = ModelGateway(RouteResolution(provider_id=provider.id, model=req.model, client=client,
        reasoning_effort=req.reasoning_effort), max_output_tokens=4096,
        call_store=request.app.state.runtime.event_store, scene_id="", purpose="capability_probe")
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


@router.get("/reservations")
async def get_model_reservations(request: Request, scene_id: str | None = None, subject: str | None = None,
                                 user: str = Depends(get_current_user)):
    """Current billing-day holds and available balance; reads only."""
    return await request.app.state.runtime.query_service.model_reservations(scene_id, subject=subject)


@router.get("/usage")
async def get_model_usage(request: Request, scene_id: str | None = None, since: float | None = None, until: float | None = None,
                          purpose: str | None = None, status: str | None = None, job_id: str | None = None, page: int = Query(1,ge=1),
                          page_size: int = Query(30,ge=1,le=100), user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.model_usage(scene_id,since=since,until=until,purpose=purpose,status=status,job_id=job_id,page=page,page_size=page_size)


@router.get("/usage/{call_id}")
async def model_call_detail(call_id: str, request: Request, scene_id: str | None = None, user: str = Depends(get_current_user)):
    result=await request.app.state.runtime.query_service.model_call(call_id,scene_id)
    if result is None:raise HTTPException(404,"调用记录不存在")
    return result
