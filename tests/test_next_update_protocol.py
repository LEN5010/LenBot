"""Release metadata, plugin compatibility and stopped-data recovery boundaries."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from len_bot.next.maintenance.snapshot import create, restore
from len_bot.next.maintenance.upgrade import inspect
from len_bot.next.instance_lock import instance_lock, InstanceBusyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/updater'))
from common import release_manifest, version_key


def test_release_order_and_protocol():
    assert sorted(['0.2.0', '0.2.0rc1', '0.2.0b2', '0.2.0a1', '0.1.9'], key=version_key) == [
        '0.1.9', '0.2.0a1', '0.2.0b2', '0.2.0rc1', '0.2.0']
    with pytest.raises(ValueError, match='Unsupported'):
        version_key('0.2.0-nightly')
    with pytest.raises(ValueError, match='protocol'):
        release_manifest({'manifest_version': 1, 'updater_protocol': 2})
    with pytest.raises(ValueError, match='differ'):
        release_manifest({'manifest_version': 1, 'updater_protocol': 1, 'version': '0.2.0', 'tag': 'v0.3.0'})


def test_snapshot_restores_root_and_external_data_without_snapshotting_lock(tmp_path):
    root, outside = tmp_path / 'instance', tmp_path / 'external'
    root.mkdir()
    outside.mkdir()
    (root / 'lenbot.config.json').write_text('{"config_version":1}')
    (root / 'data.sqlite3-wal').write_bytes(b'wal-fixture')
    (outside / 'memory.md').write_text('升级前正文')
    snapshot = tmp_path / 'backup'
    with instance_lock(root):
        create([str(root), str(outside)], snapshot)
        assert not (snapshot / '0/.lenbot-instance.lock').exists()
        (root / 'lenbot.config.json').write_text('{"config_version":2}')
        (root / 'new.json').write_text('new')
        (outside / 'memory.md').write_text('升级后正文')
        restore(root, snapshot)
        with pytest.raises(InstanceBusyError):
            with instance_lock(root):
                pass
    assert json.loads((root / 'lenbot.config.json').read_text()) == {'config_version': 1}
    assert (root / 'data.sqlite3-wal').read_bytes() == b'wal-fixture'
    assert (outside / 'memory.md').read_text() == '升级前正文'
    assert not (root / 'new.json').exists()


def test_target_version_blocks_enabled_plugin_without_importing_it(tmp_path):
    plugin = tmp_path / 'plugins/fixture'
    plugin.mkdir(parents=True)
    (plugin / '__init__.py').write_text('raise AssertionError("inspection must not import plugin code")')
    (plugin / 'plugin.toml').write_text('''name = "fixture"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<0.2"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["fixture"]
license = "GPL-3.0-only"
description = "Compatibility fixture"
''')
    configuration = {'plugins': {'paths': ['plugins'], 'fixture': {}, 'disabled': []}}
    path = tmp_path / 'lenbot.config.json'
    path.write_text(json.dumps(configuration))
    assert inspect(tmp_path, '0.1.1')['blocked_plugins'] == []
    assert 'requires host' in inspect(tmp_path, '0.2.0')['blocked_plugins'][0]
    configuration['plugins']['disabled'] = ['fixture']
    path.write_text(json.dumps(configuration))
    assert inspect(tmp_path, '0.2.0')['blocked_plugins'] == []
