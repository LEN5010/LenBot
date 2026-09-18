import uuid

from fastapi import APIRouter, Request, Depends, HTTPException, Body
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, TypeAdapter, model_validator
from len_bot.config import AddressName
from len_bot.config_edit import ConfigEdit, ConfigEditConflict
from len_bot.config_store import AccessSettings, ResourceSettings, TimeSettings, MemberSettings
from len_bot.runtime.capabilities import Capability, CapabilityGrant
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def save_runtime_settings(runtime, values, *, live, baseline):
    try:
        await runtime.update_runtime_settings(values, live=live, baseline=baseline)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except ConfigEditConflict:
        raise
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败，原运行设置未发布：" + str(error.strerror)) from error


async def save_root_section(runtime, section, values, *, baseline):
    try:
        await runtime.update_root_settings(section, values, baseline=baseline)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_input=False, include_context=False)) from error
    except ConfigEditConflict:
        raise
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(500, "配置文件保存失败，原运行设置未发布：" + str(error.strerror)) from error


@router.get("/draft/{domain}")
async def settings_draft(domain: str, request: Request, user: str = Depends(get_current_user)):
    try:
        return request.app.state.runtime.query_service.settings_draft(domain)
    except KeyError:
        raise HTTPException(404, '未知配置领域') from None


@router.get("/access")
async def access_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.access_settings()


CAPABILITY_TITLES = {
    'long_work': '后台工作与恢复',
    'public_research': '系统公共研究（只读 global-safe）',
    'network_python': '联网 Python（需 gateway、已核验的出口策略；群资料工作不会因没有图片而放行）',
    'proactive_chat': '主动聊天（尚未实现）',
    'interest_share': '公共兴趣分享（须启用插件与本群配置）',
    'send_file': '发送文件（须配置文件交付及已核对的 OneBot 上传能力）',
    'bilibili_authenticated_read': 'B 站登录态动态读取（人类工作、已配置账号与审查）',
    'bilibili_like': 'B 站点赞（专用账号、额度和动作审查）',
    'bilibili_favorite': 'B 站收藏（专用账号、指定收藏夹和动作审查）',
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
    implemented = {'long_work', 'public_research', 'network_python', 'interest_share', 'send_file', 'bilibili_authenticated_read', 'bilibili_like', 'bilibili_favorite'}
    return {'items': [{'value': capability.value,
                       'title': CAPABILITY_TITLES.get(capability.value, capability.value),
                       'implemented': capability.value in implemented}
                      for capability in Capability]}


class CapabilityGrantEdit(BaseModel):
    """Fields an operator may submit; the issuer is bound after authentication."""
    model_config = ConfigDict(extra='forbid')
    grant_id: str = Field(default='', max_length=64,
        description='已有授予的 ID；新建留空，由服务端生成')
    revision: int = Field(default=1, ge=1)
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
    qq_reply_whitelist: list[int] | None = None
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
async def update_access_settings(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    values = AccessSettingsRequest.model_validate(edit.values)
    changed = False
    merged = None

    def merge_current(saved, baseline):
        nonlocal changed, merged
        current = AccessSettings.model_validate(saved)
        # Lists have deliberate replacement semantics. Check only the list
        # being edited, including concurrent additions and removals.
        for field in values.model_fields_set:
            if getattr(values, field) is not None:
                if not isinstance(baseline, dict) or field not in baseline:
                    raise HTTPException(422, '缺少访问设置草稿基线')
                if saved[field] != baseline[field]:
                    from len_bot.config_edit import ConfigEditConflict
                    raise ConfigEditConflict(('access', field))
        if values.capability_grants is None:
            grants = list(current.capability_grants)
        else:
            previous = {grant.grant_id: grant for grant in current.capability_grants}
            grants = []
            for index, edit in enumerate(values.capability_grants):
                if not edit.grant_id:
                    created = edit.model_copy(update={'grant_id': 'g' + uuid.uuid4().hex})
                    grants.append(_grant_from_edit(created, operator_id=user, revision=1))
                    continue
                stored = previous.get(edit.grant_id)
                if stored is None:
                    raise HTTPException(422, [{
                        'loc': ['body', 'capability_grants', index, 'grant_id'],
                        'msg': '不能用未知 ID 新建授予；新建请留空，由服务端生成 ID',
                        'type': 'value_error'}])
                if edit.revision != stored.revision:
                    raise HTTPException(409, '授予已被其他操作更新，请保留草稿并刷新已保存值后重试')
                candidate = _grant_from_edit(edit, operator_id=user, revision=edit.revision)
                if _grant_content(stored) == _grant_content(candidate):
                    grants.append(stored)
                else:
                    grants.append(_grant_from_edit(edit, operator_id=user, revision=stored.revision + 1))
        merged = AccessSettings(
            qq_reply_whitelist=(values.qq_reply_whitelist if values.qq_reply_whitelist is not None
                                else current.qq_reply_whitelist),
            capability_grants=grants,
        ).model_dump()
        changed = merged['capability_grants'] != current.model_dump()['capability_grants']
        return merged

    await save_root_section(runtime, "access", merge_current, baseline=edit.baseline)
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
async def update_resource_settings(request: Request, edit: ConfigEdit, user: str = Depends(get_current_user)):
    """Quota policies live with the capabilities that reference them by name."""
    runtime = request.app.state.runtime
    await save_root_section(runtime, "resources", ResourceSettings.model_validate(edit.values).model_dump(), baseline=edit.baseline)
    return {"settings": runtime.query_service.resource_settings(),
            "message": "额度策略已写入根文件；新策略用于此后新建的工作，已预占的工作保留自己的策略"}


@router.put("/time")
async def update_time_settings(request: Request, edit: ConfigEdit, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await save_root_section(runtime, "time", TimeSettings.model_validate(edit.values).model_dump() if edit.values is not None else None, baseline=edit.baseline)
    return {"settings": runtime.query_service.time_settings(), "requires_restart": runtime.restart_required,
            "message": "业务时间设置已写入根文件，按页面提示重启后用于新查询"}


@router.get("/members")
async def member_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.member_settings()


@router.put("/members")
async def update_member_settings(request: Request, edit: ConfigEdit, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    await save_root_section(runtime, "members", [member.model_dump() for member in TypeAdapter(list[MemberSettings]).validate_python(edit.values)], baseline=edit.baseline)
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
async def update_persona_settings(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    req = PersonaSettingsRequest.model_validate(edit.values)
    values = {key: value.strip() if isinstance(value, str) else value
              for key, value in req.model_dump(exclude_none=True).items()}
    await save_runtime_settings(runtime, values, live=True, baseline=edit.baseline)
    return {"success": True, "message": "人格与说话风格已保存，并立即生效"}


class AttentionSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attention_keywords: list[str] | None = None
    attention_observation_interval_seconds: float | None = None
    attention_observation_enabled: bool | None = None
    attention_keyword_cooldown_seconds: float | None = None
    attention_focus_seconds: float | None = None
    addressed_debounce_idle_ms: int | None = None
    addressed_debounce_max_ms: int | None = None
    observing_debounce_idle_ms: int | None = None
    observing_debounce_max_ms: int | None = None
    conversation_recent_tokens: int | None = None
    scene_hourly_message_limit: int | None = None
    user_hourly_message_limit: int | None = None


@router.get("/attention")
async def get_attention_settings(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.attention_settings()


@router.patch("/attention")
async def update_attention_settings(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    values = AttentionSettingsRequest.model_validate(edit.values).model_dump(exclude_unset=True)
    await save_runtime_settings(runtime, values, live=True, baseline=edit.baseline)
    return {"success": True, "message": "注意力参数已写入根配置，下次扫描起生效", "settings": runtime.query_service.attention_settings()}


@router.get("/runtime")
async def runtime_parameters(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.runtime_settings()


@router.patch("/runtime")
async def update_runtime_parameters(edit: ConfigEdit, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    current = runtime.query_service.runtime_settings()["settings"]
    if not isinstance(edit.values, dict) or not set(edit.values) <= set(current):
        raise HTTPException(422, "只允许修改页面提供的运行参数字段")
    await save_runtime_settings(runtime, edit.values, live=False, baseline=edit.baseline)
    return {**runtime.query_service.runtime_settings(),
            "message": "运行参数已写入根配置；五项执行预算用于新对话和新工作执行段，其他待生效改动需手动重启"}
