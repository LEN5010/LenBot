from fastapi import APIRouter, Request, Depends, HTTPException, Body
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from len_bot.config import AddressName
from len_bot.config_store import AccessSettings, TimeSettings, MemberSettings
from len_bot.runtime.capabilities import CapabilityGrant
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def save_runtime_settings(runtime, values, *, live):
    try:
        await runtime.update_runtime_settings(values, live=live)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败，原运行设置未发布：" + str(error.strerror)) from error


async def save_root_section(runtime, section, values):
    try:
        await runtime.update_root_settings(section, values)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败，原运行设置未发布：" + str(error.strerror)) from error


@router.get("/access")
async def access_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.access_settings()


class AccessSettingsRequest(BaseModel):
    """The reply white list and the capability grants save through one page.

    Omitting `capability_grants` keeps the current grants, so an older panel
    that only sends the white list cannot silently wipe an operator's
    authorization.
    """
    model_config = ConfigDict(extra="forbid")
    qq_reply_whitelist: list[int]
    capability_grants: list[CapabilityGrant] | None = None


@router.put("/access")
async def update_access_settings(values: AccessSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    current = runtime.config_store.current.access
    # The issuer is whoever is authenticated on this panel, not a field the
    # form can type.  A group admin or a chat message cannot reach this route.
    grants = [grant.model_copy(update={'operator_id': user})
              for grant in (current.capability_grants if values.capability_grants is None
                            else values.capability_grants)]
    merged = AccessSettings(
        qq_reply_whitelist=values.qq_reply_whitelist,
        capability_grants=grants,
    ).model_dump()
    changed = merged['capability_grants'] != current.model_dump()['capability_grants']
    await save_root_section(runtime, "access", merged)
    if changed:
        # A grant revision is an operator action; the saved file is the
        # effective version from this point, and an already-sent message
        # cannot be recalled by revoking later.
        await runtime.record_operator_event("system:settings", "capability_grants", user,
            {"grant_ids": [grant['grant_id'] for grant in merged['capability_grants']]})
    return {"settings": runtime.query_service.access_settings(), "requires_restart": False,
            "message": "QQ 回复白名单与能力授予已保存；未配置的能力保持关闭，撤销只阻止后续操作"}


@router.get("/time")
async def time_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.time_settings()


@router.put("/time")
async def update_time_settings(request: Request, values: TimeSettings | None = Body(...), user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await save_root_section(runtime, "time", values.model_dump() if values is not None else None)
    return {"settings": runtime.query_service.time_settings(), "requires_restart": runtime.restart_required,
            "message": "业务时间设置已写入根文件，按页面提示重启后用于新查询"}


@router.get("/members")
async def member_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.member_settings()


@router.put("/members")
async def update_member_settings(request: Request, values: list[MemberSettings] = Body(...), user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await save_root_section(runtime, "members", [member.model_dump() for member in values])
    return {"settings": runtime.query_service.member_settings(), "requires_restart": runtime.restart_required,
            "message": "成员名称、别名与 B 站身份已写入根文件，重启后用于新查询与采集"}


@router.post("/reset")
async def reset_conversation_data(request: Request, user: str = Depends(get_current_user)):
    result = await request.app.state.runtime.reset_conversation_data(user)
    request.app.state.log_ring.clear()
    return result

@router.get("/persona/diana")
async def preview_diana(request: Request, user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.preview_diana_persona()


class PersonaSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_context: Optional[str] = None
    identity_name: str
    identity_persona: str
    identity_core: Optional[str] = None
    conversation_style: Optional[str] = None
    address_names: list[AddressName]|None = Field(default=None,max_length=32)

@router.get("/persona")
async def get_persona_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.persona_settings()

@router.post("/persona")
async def update_persona_settings(req: PersonaSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    values = {
        key: getattr(runtime.config, key) for key in
        ("character_context", "identity_name", "identity_core", "identity_persona",
         "conversation_style", "address_names")
    }
    values.update({key: value.strip() if isinstance(value, str) else value
                   for key, value in req.model_dump(exclude_none=True).items()})
    await save_runtime_settings(runtime, values, live=True)
    return {"success": True, "message": "人格与说话风格已保存，并立即生效"}


class AttentionSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attention_keywords: list[str] | None = None
    attention_sample_window_seconds: float | None = None
    attention_sample_probability: float | None = None
    attention_keyword_cooldown_seconds: float | None = None
    attention_focus_seconds: float | None = None
    conversation_recent_tokens: int | None = None


@router.get("/attention")
async def get_attention_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.attention_settings()


@router.patch("/attention")
async def update_attention_settings(req: AttentionSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    current = runtime.query_service.attention_settings()
    values = {**current, **req.model_dump(exclude_unset=True)}
    await save_runtime_settings(runtime, values, live=True)
    return {"success": True, "message": "注意力参数已写入根配置，下次扫描起生效", "settings": values}


@router.get("/runtime")
async def runtime_parameters(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.runtime_settings()


@router.patch("/runtime")
async def update_runtime_parameters(values: dict, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    current = runtime.query_service.runtime_settings()["settings"]
    if set(values) != set(current):
        raise HTTPException(422, "运行参数节必须完整填写页面提供的字段")
    await save_runtime_settings(runtime, values, live=False)
    return {**runtime.query_service.runtime_settings(),
            "message": "运行参数已写入根配置；五项执行预算用于新对话和新工作执行段，其他待生效改动需手动重启"}
