"""The panel acts as the instance owner: whoever is logged in already controls the whole instance."""

from fastapi import HTTPException

from ...runtime.network import NetworkRuntime


def panel_owner(runtime: NetworkRuntime) -> str:
    if not runtime.config.owners:
        raise HTTPException(409, '设置里还没有主人账号')
    return runtime.config.owners[0]
