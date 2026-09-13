"""This project's own capability vocabulary and its single permission check.

Capability grants are operator-owned root configuration (`access.capability_grants`).
Nothing in this module is reachable from a model proposal: the model may
reference a real event and ask for ordinary work, but it cannot invent an
initiator and it cannot write a grant.  There is deliberately no second,
editable access-control list in the database.

Check order (plan M04).  This module owns the first three steps and reports
which one refused:

    1. current scene            `ScenePolicy` decides this before delegating
    2. real source and initiator `initiator` is built from a stored event
    3. current grant             `check()` below

The remaining steps stay where they already are and are not duplicated here:

    4. original work revision   `job_store.validate_job_proposals_in_transaction`
    5. the tool/operation's own requirement  `plugins/host.py`
    6. data scope               `tools/retrieval.py` read scopes
    7. budget                   the shared budget reservation in a later commit

Verification only decides permission.  It never infers an authorization from
how the model phrased a request.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.events.models import HumanInitiator, PluginInitiator, SystemInitiator


class Capability(StrEnum):
    """The plan's capability set.  A tool name is never a permission."""
    LONG_WORK = 'long_work'
    PUBLIC_RESEARCH = 'public_research'
    NETWORK_PYTHON = 'network_python'
    PROACTIVE_CHAT = 'proactive_chat'
    INTEREST_SHARE = 'interest_share'
    SEND_FILE = 'send_file'
    BILIBILI_AUTHENTICATED_READ = 'bilibili_authenticated_read'
    BILIBILI_LIKE = 'bilibili_like'
    BILIBILI_FAVORITE = 'bilibili_favorite'


# What an autonomous, non-human source must be granted before it may start a
# work item.  Ordinary human work is unaffected: the existing chat white list
# and the existing work checks keep deciding it, and ordinary conversation
# never needs `long_work`.
SYSTEM_WORK_CAPABILITIES: dict[str, tuple[Capability, ...]] = {
    'information': (Capability.PUBLIC_RESEARCH,),
}


class CapabilityGrant(BaseModel):
    """One operator-issued grant.  Quota numbers stay in the resource policy.

    `resource_policy` references an existing policy by name; this record does
    not copy an editable default of its own, and it grants nothing implicitly:
    an absent, disabled or expired grant denies.
    """
    model_config = ConfigDict(extra='forbid', strict=True)

    grant_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    operator_id: str = Field(min_length=1)
    principal_type: Literal['human', 'system', 'plugin']
    principal_id: str = Field(min_length=1)
    scene_id: str | None = Field(default=None, description='只对这一个场景生效；与 system_scope 二选一')
    system_scope: str | None = Field(default=None, description='明确的系统范围；只有 system 主体可以填写')
    capabilities: list[Capability] = Field(min_length=1)
    expires_at: float | None = Field(default=None, description='绝对 Unix 时间；空表示长期有效')
    resource_policy: str | None = Field(default=None, description='既有资源策略的名称引用，不在这里复制额度数值')
    concurrency: int | None = Field(default=None, ge=1)
    enabled: bool

    @field_validator('principal_id', 'grant_id', 'operator_id', mode='before')
    @classmethod
    def text_identifiers(cls, value):
        # The root file is hand-written JSON, so a QQ account may arrive as a
        # bare number the way `qq_reply_whitelist` already allows.  Normalize
        # it to text here instead of failing with a type error.
        return str(value) if isinstance(value, int) and not isinstance(value, bool) else value

    @field_validator('capabilities', mode='before')
    @classmethod
    def known_capabilities(cls, value):
        # The root file is JSON, so a capability arrives as its own name.  Map
        # it here and let the enum reject anything outside the vocabulary
        # rather than silently accepting an unknown capability.
        if isinstance(value, list):
            return [Capability(item) if isinstance(item, str) and not isinstance(item, Capability) else item
                    for item in value]
        return value

    @model_validator(mode='after')
    def scoped_and_unambiguous(self):
        if (self.scene_id is None) == (self.system_scope is None):
            raise ValueError('能力授予必须且只能选择场景或明确的系统范围之一')
        if self.system_scope is not None and self.principal_type != 'system':
            raise ValueError('只有系统主体可以使用系统范围；人类与插件授予必须指定场景')
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError('同一条授予不能重复列出同一个能力')
        if self.expires_at is not None and self.expires_at <= 0:
            raise ValueError('能力授予的有效期必须是绝对 Unix 时间')
        return self


def validate_grants(grants: list[CapabilityGrant]) -> list[CapabilityGrant]:
    """One grant_id names one principal, scope and revision only once."""
    seen: set[str] = set()
    for grant in grants:
        if grant.grant_id in seen:
            raise ValueError(f'能力授予 ID 重复：{grant.grant_id}')
        seen.add(grant.grant_id)
    return grants


@dataclass(frozen=True)
class CapabilitySubject:
    """Who is asking, taken from a real stored source rather than a claim."""
    principal_type: Literal['human', 'system', 'plugin']
    principal_id: str
    scene_id: str | None
    system_scope: str | None

    @property
    def billing_subject(self) -> str:
        if self.principal_type == 'human':
            return 'user:' + self.principal_id
        return 'system:' + (self.system_scope or self.principal_id)


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    step: str
    reason: str
    grant_id: str | None = None

    def __bool__(self) -> bool:
        return self.allowed


def subject_for(initiator, scene_id: str | None) -> CapabilitySubject:
    """A typed initiator is the only way to become a capability subject."""
    if isinstance(initiator, HumanInitiator):
        return CapabilitySubject('human', initiator.user_id, scene_id, None)
    if isinstance(initiator, SystemInitiator):
        return CapabilitySubject('system', initiator.agent_id, None,
                                 initiator.purpose or 'unspecified')
    if isinstance(initiator, PluginInitiator):
        return CapabilitySubject('plugin', initiator.plugin_id, scene_id, None)
    raise ValueError('能力检查需要明确的发起者类型，不接受空主体或未声明的来源')


class CapabilityAuthority:
    """Reads the current grant set from the one editable source: root config."""

    def __init__(self, config_store, scene_policy=None):
        self.config_store = config_store
        self.scene_policy = scene_policy

    def grants(self) -> list[CapabilityGrant]:
        return list(self.config_store.current.access.capability_grants)

    def grant_for(self, subject: CapabilitySubject, capability: Capability, now: float) -> CapabilityGrant | None:
        for grant in self.grants():
            if not grant.enabled or grant.principal_type != subject.principal_type:
                continue
            if grant.principal_id != subject.principal_id:
                continue
            if capability not in grant.capabilities:
                continue
            if grant.scene_id is not None and grant.scene_id != subject.scene_id:
                continue
            if grant.system_scope is not None and grant.system_scope != subject.system_scope:
                continue
            if grant.expires_at is not None and grant.expires_at <= now:
                continue
            return grant
        return None

    def check(self, capability: Capability, subject: CapabilitySubject, *, now: float) -> CapabilityDecision:
        """The current grant is the whole of this decision; absence denies."""
        if subject.scene_id is not None and self.scene_policy is not None:
            if not self.scene_policy.enabled(subject.scene_id):
                return CapabilityDecision(False, '当前场景', '本场景未启用，能力检查在此结束')
        grant = self.grant_for(subject, capability, now)
        if grant is None:
            return CapabilityDecision(False, '当前 grant',
                f'当前配置没有向该主体授予 {capability.value}；未配置的能力保持关闭')
        return CapabilityDecision(True, '当前 grant', f'由授予 {grant.grant_id} 第 {grant.revision} 版允许', grant.grant_id)

    def required_for_work(self, work_operation: str) -> tuple[Capability, ...]:
        return SYSTEM_WORK_CAPABILITIES.get(work_operation, ())
