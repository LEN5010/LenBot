"""Actual 账号 identities shared by task and schedule capability checks."""
from collections.abc import Sequence
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


def roles_for(requester: str, *, owners: Sequence[str], scoped_owner: str | None,
              admins: Sequence[str], whitelist: Sequence[str], group_role: str | None) -> set[str]:
    roles = {'member'}
    if requester in owners or requester == scoped_owner:
        roles.add('owner')
    if requester in admins:
        roles.add('admin')
    if requester in whitelist:
        roles.add('whitelist')
    if group_role in {'owner', 'admin'}:
        roles.add('group_manager')
    return roles


class IdentitySettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    admins: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)

    @field_validator('admins', 'whitelist', 'blacklist')
    @classmethod
    def actual_identities(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(re.fullmatch(r'[a-z][a-z0-9_-]*:[^:\s/\\]+', value) is None for value in values):
            raise ValueError('must contain distinct platform:account identities')
        return values


def combine_identities(global_settings: IdentitySettings, local: IdentitySettings | None) -> IdentitySettings:
    if local is None:
        return global_settings
    return IdentitySettings.model_construct(**{
        field: list(dict.fromkeys([*getattr(global_settings, field), *getattr(local, field)]))
        for field in IdentitySettings.model_fields
    })
