from fastapi import APIRouter, Request, Depends, HTTPException, Body
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from len_bot.config import AddressName
from len_bot.config_store import AccessSettings, ResourceSettings, TimeSettings, MemberSettings
from len_bot.runtime.capabilities import Capability, CapabilityGrant
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


CAPABILITY_TITLES = {
    'long_work': '后台工作与恢复',
    'public_research': '系统公共研究（只读 global-safe）',
    'network_python': '联网 Python（需 gateway 后端且网关已建出口策略）',
    'proactive_chat': '主动聊天（尚未实现）',
    'interest_share': '公共兴趣分享（尚未实现）',
    'send_file': '发送文件（尚未实现）',
    'bilibili_authenticated_read': 'B 站登录态读取（尚未实现）',
    'bilibili_like': 'B 站点赞（尚未实现）',
    'bilibili_favorite': 'B 站收藏（尚未实现）',
}


@router.get("/capabilities")
async def capability_vocabulary(user: str = Depends(get_current_user)):
    """The vocabulary an operator may grant, with which entries are implemented.

    The panel offers these names as a choice and states the ones that are
    only vocabulary; naming a capability was never the same as implementing
    it, and a grant for an unimplemented capability still permits nothing.

    ``network_python`` is implemented in the sense that it is checked where an
    execution is admitted — but only the Gateway backend can carry egress at
    all (the local trial worker runs ``--network none``), and only for a
    deployment that built a proxy policy.  The title says so, because a grant
    on its own still permits nothing.
    """
    implemented = {'long_work', 'public_research', 'network_python'}
    return {'items': [{'value': capability.value,
                       'title': CAPABILITY_TITLES.get(capability.value, capability.value),
                       'implemented': capability.value in implemented}
                      for capability in Capability]}


class CapabilityGrantEdit(BaseModel):
    """Fields an operator may submit; the issuer is bound after authentication."""
    model_config = ConfigDict(extra='forbid')
    grant_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    principal_type: str
    principal_id: str = Field(min_length=1)
    scene_id: str | None = None
    system_scope: str | None = None
    capabilities: list[str] = Field(min_length=1)
    expires_at: float | None = None
    resource_policy: str | None = None
    concurrency: int | None = Field(default=None, ge=1)
    enabled: bool

    @model_validator(mode='after')
    def matching_scope(self):
        if self.principal_type == 'system':
            self.scene_id = None
            if not self.system_scope:
                raise ValueError('系统授予必须填写系统范围，不能残留场景字段')
        else:
            self.system_scope = None
            if not self.scene_id:
                raise ValueError('人类与插件授予必须填写场景，不能残留系统范围')
        return self


class AccessSettingsRequest(BaseModel):
    """The reply white list and the capability grants save through one page.

    Omitting `capability_grants` keeps the current grants, so an older panel
    that only sends the white list cannot silently wipe an operator's
    authorization.
    """
    model_config = ConfigDict(extra="forbid")
    qq_reply_whitelist: list[int]
    capability_grants: list[CapabilityGrantEdit] | None = None


def _grant_from_edit(edit: CapabilityGrantEdit, *, operator_id: str, revision: int) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=edit.grant_id, revision=revision, operator_id=operator_id,
        principal_type=edit.principal_type, principal_id=edit.principal_id,
        scene_id=edit.scene_id, system_scope=edit.system_scope,
        capabilities=[Capability(item) for item in edit.capabilities],
        expires_at=edit.expires_at, resource_policy=edit.resource_policy or None,
        concurrency=edit.concurrency, enabled=edit.enabled)


def _grant_content(grant: CapabilityGrant) -> dict:
    return grant.model_dump(exclude={'operator_id', 'revision'})


@router.put("/access")
async def update_access_settings(values: AccessSettingsRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    current = runtime.config_store.current.access
    if values.capability_grants is None:
        grants = list(current.capability_grants)
    else:
        previous = {grant.grant_id: grant for grant in current.capability_grants}
        grants = []
        for edit in values.capability_grants:
            stored = previous.get(edit.grant_id)
            candidate = _grant_from_edit(edit, operator_id=user, revision=edit.revision)
            if stored is not None and _grant_content(stored) == _grant_content(candidate):
                grants.append(stored)
            else:
                revision = (stored.revision + 1) if stored is not None else max(1, edit.revision)
                grants.append(_grant_from_edit(edit, operator_id=user, revision=revision))
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


@router.get("/resources")
async def resource_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.resource_settings()


@router.put("/resources")
async def update_resource_settings(request: Request, values: ResourceSettings, user: str = Depends(get_current_user)):
    """Quota policies live with the capabilities that reference them by name."""
    runtime = request.app.state.runtime
    await save_root_section(runtime, "resources", values.model_dump())
    return {"settings": runtime.query_service.resource_settings(),
            "message": "额度策略已写入根文件；新策略用于此后新建的工作，已预占的工作保留自己的策略"}


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
