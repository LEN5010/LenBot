"""Compare an operator's original values inside the existing config lock."""
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConfigEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    baseline: Any
    values: Any


class ConfigEditConflict(ValueError):
    def __init__(self, path):
        self.path = list(path)
        super().__init__('配置已被其他操作修改：' + '.'.join(path) + '；草稿未保存，请核对最新值')


_MISSING = object()


def merge_edit(current, baseline, desired, path=()):
    """Unchanged fields keep today's value; changed leaves must match their baseline.

    Lists are intentional whole-list edits. Dictionary keys may be added or
    removed, but removal checks the original value just like replacement.
    No version table, digest or second configuration source is involved.
    """
    if baseline == desired:
        return deepcopy(current) if current is not _MISSING else _MISSING
    if isinstance(baseline, dict) and isinstance(desired, dict) and isinstance(current, dict):
        result = deepcopy(current)
        for key in baseline.keys() | desired.keys():
            value = merge_edit(current.get(key, _MISSING), baseline.get(key, _MISSING),
                               desired.get(key, _MISSING), (*path, key))
            if value is _MISSING:
                result.pop(key, None)
            else:
                result[key] = value
        return result
    if current != baseline:
        raise ConfigEditConflict(path)
    return deepcopy(desired) if desired is not _MISSING else _MISSING


def merge_fields(current, baseline, desired, path=()):
    """A runtime form owns only the keys that it explicitly submits."""
    if not isinstance(baseline, dict) or not isinstance(desired, dict):
        raise ValueError('配置表单必须提供对象形式的基线与改动值')
    result = deepcopy(current)
    for key, value in desired.items():
        if key not in baseline or key not in current:
            raise ValueError('配置基线缺少字段：' + '.'.join((*path, key)))
        result[key] = merge_edit(current[key], baseline[key], value, (*path, key))
    return result
