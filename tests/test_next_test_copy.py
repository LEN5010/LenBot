"""Stopped-instance copy permissions and configuration boundaries."""

import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from len_bot.next.config import load_host_config
from len_bot.next.instance_lock import instance_lock


def source_instance(root: Path) -> dict:
    root.mkdir()
    source = {
        'mode': 'isolated-multi', 'bot_qq': '90001', 'owner_qq': '70001',
        'timezone': 'UTC', 'database': 'state.db', 'delivery': 'onebot',
        'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9', 'access_token': 'fixture'},
        'panel': {'host': '127.0.0.1', 'port': 8088, 'username': 'fixture',
                  'password_hash': 'fixture$' + '0' * 64},
        'compaction': {'input_tokens': 2000},
        'models': {'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                            'api_key': 'fixture'}},
                   'roles': {'mind': {'provider': 'fixture', 'model': 'fixture',
                                      'context_window_tokens': 8192}}},
        'plugins': {'paths': ['plugins'], 'data_directory': 'plugin-data'},
        'scenes': {'group:80001': {'persona': 'role'}},
    }
    path = root / 'lenbot.config.json'
    path.write_text(json.dumps(source))
    path.chmod(0o600)
    (root / 'state.db').write_bytes(b'fixture database bytes')
    (root / 'state.db').chmod(0o640)
    (root / 'message-link').symlink_to('state.db')
    return source


def command(source: Path, target: Path, port: int) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'len_bot.next.maintenance.test_copy',
                           str(source), str(target), '--panel-port', str(port)],
                          capture_output=True, text=True)


def test_copy_preserves_files_permissions_and_other_configuration(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    original = source_instance(source)
    result = command(source, target, 8089)
    assert result.returncode == 0, result.stderr
    assert str(source) in result.stdout and str(target) in result.stdout and '8089' in result.stdout
    copied = json.loads((target / 'lenbot.config.json').read_text())
    expected = {**original, 'delivery': 'simulated', 'onebot': None,
                'panel': {**original['panel'], 'port': 8089}}
    assert copied == expected
    assert json.loads((source / 'lenbot.config.json').read_text()) == original
    assert stat.S_IMODE((target / 'lenbot.config.json').stat().st_mode) == 0o600
    assert stat.S_IMODE((target / 'state.db').stat().st_mode) == 0o640
    assert (target / 'state.db').read_bytes() == (source / 'state.db').read_bytes()
    assert (target / 'message-link').readlink() == Path('state.db')
    assert load_host_config(target).plugins is not None


@pytest.mark.parametrize('blocked', ['exists', 'busy', 'invalid_port'])
def test_copy_reports_original_boundary_error(tmp_path, blocked):
    source, target = tmp_path / 'source', tmp_path / 'target'
    original = source_instance(source)
    if blocked == 'exists':
        target.mkdir()
        result = command(source, target, 8089)
        assert 'FileExistsError' in result.stderr
        assert not list(target.iterdir())
    elif blocked == 'busy':
        with instance_lock(source.resolve()):
            result = command(source, target, 8089)
        assert 'InstanceBusyError' in result.stderr
        assert not target.exists()
    else:
        result = command(source, target, 65536)
        assert 'ValidationError' in result.stderr and 'panel.port' in result.stderr
        assert json.loads((target / 'lenbot.config.json').read_text()) == original
    assert result.returncode != 0
    assert json.loads((source / 'lenbot.config.json').read_text()) == original
