"""Actual QQ identities shared by task and schedule capability checks."""
from collections.abc import Sequence


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
