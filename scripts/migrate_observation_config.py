"""One-time, offline attention configuration conversion; never used at startup.

Stop LenBot and back up its root configuration before running with --write.
Without --write, report the field paths that would change and validate the
converted configuration. No configuration values or credentials are printed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile

from pydantic import ValidationError

from len_bot.config_store import RootConfig
from len_bot.plugins.catalog import PluginCatalog


def convert_attention(values, *, global_settings, path):
    changed = []
    prefix = 'attention_' if global_settings else ''
    for old, new in ((prefix + 'sample_probability', prefix + 'observation_enabled'),
                     (prefix + 'sample_window_seconds', prefix + 'observation_interval_seconds')):
        if old not in values:
            continue
        if new in values:
            raise ValueError(f'{path} 同时存在 {old} 与 {new}，请离线明确保留哪个值')
        value = values.pop(old)
        if old.endswith('sample_probability') and value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'{path}.{old} 不是原配置允许的概率数值')
            value = value > 0
        values[new] = value
        changed.append(f'{path}.{old} -> {path}.{new}')
    if global_settings:
        for key in ('attention_engagement_step', 'attention_engagement_recovery_seconds'):
            if key in values:
                del values[key]
                changed.append(f'删除 {path}.{key}')
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='停机并完成备份后，写回项目根目录 lenbot.config.json')
    options = parser.parse_args()
    source = Path.cwd() / 'lenbot.config.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    changes = convert_attention(data['runtime'], global_settings=True, path='runtime')
    for scene_id, scene in data['scenes'].items():
        if scene.get('attention') is not None:
            changes.extend(convert_attention(scene['attention'], global_settings=False,
                                             path=f'scenes.{scene_id}.attention'))
    catalog = PluginCatalog.discover(data['plugin_directories'])
    try:
        RootConfig.model_validate(data, context={'plugin_catalog': catalog})
    except ValidationError as error:
        fields = '; '.join('.'.join(map(str, item['loc'])) + ': ' + item['msg']
                           for item in error.errors(include_input=False, include_context=False))
        raise ValueError('转换后配置校验失败：' + fields) from None
    print('\n'.join(changes) if changes else '没有需要转换的观察字段')
    if not options.write or not changes:
        print('配置未写入；转换结果符合当前配置结构')
        return
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    descriptor, name = tempfile.mkstemp(prefix='.lenbot-observation-', suffix='.json', dir=source.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), source.stat().st_mode & 0o777)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, source)
    finally:
        temporary.unlink(missing_ok=True)
    print('已写入 lenbot.config.json；请按运行手册记录转换并在取得启动授权后启动')


if __name__ == '__main__':
    main()
