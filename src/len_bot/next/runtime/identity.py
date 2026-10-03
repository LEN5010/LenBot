"""Actual QQ identities shared by task and schedule capability checks."""
from collections.abc import Sequence
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


def roles_for(requester: str, *, owner: str | None, scoped_owner: str | None,
              admins: Sequence[str], whitelist: Sequence[str], group_role: str | None) -> set[str]:
    roles = {'member'}
    if requester in {owner, scoped_owner}:
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
    def actual_qqs(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(re.fullmatch(r'[1-9][0-9]*', value) is None for value in values):
            raise ValueError('must contain distinct positive QQ numbers as text')
        return values


def combine_identities(global_settings: IdentitySettings, local: IdentitySettings | None) -> IdentitySettings:
    if local is None:
        return global_settings
    return IdentitySettings.model_construct(**{
        field: list(dict.fromkeys([*getattr(global_settings, field), *getattr(local, field)]))
        for field in IdentitySettings.model_fields
    })
