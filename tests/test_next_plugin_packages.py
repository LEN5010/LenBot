"""Plugin manifest compatibility and external ZIP ingress boundaries."""

from io import BytesIO
from pathlib import Path
import stat
import zipfile

import pytest

from len_bot.next.plugins.install import PluginInstaller
from len_bot.next.plugins.manifest import read_manifest


COUNTER = Path(__file__).parents[1] / 'developer' / 'examples' / 'counter'


def package(*, prefix='', changed_manifest=None, member=None):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name in ('plugin.toml', '__init__.py', 'README.md'):
            data = (COUNTER / name).read_bytes()
            if name == 'plugin.toml' and changed_manifest is not None:
                data = changed_manifest.encode()
            archive.writestr(prefix + name, data)
        if member is not None:
            archive.writestr(member, b'outside')
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', 'counter-v1/'])
async def test_zip_root_layout_and_source_identity(tmp_path, prefix):
    installer = PluginInstaller(tmp_path)
    manifest, record, output = await installer.prepare_zip(package(prefix=prefix), 'counter-v1.zip', [])
    assert manifest.name == record.name == 'counter'
    assert record.installed is None and record.candidate.kind == 'zip'
    assert record.candidate.version == '1.0.0' and len(record.candidate.revision) == 64
    assert record.application == 'plugin' and record.requested is False and output == ''
    assert read_manifest(installer.candidates / 'counter').version == '1.0.0'
    assert not (installer.directory / 'counter').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['../escaped', '/escaped', 'a/../../escaped', 'a\\escaped'])
async def test_zip_rejects_paths_outside_package(tmp_path, name):
    installer = PluginInstaller(tmp_path)
    with pytest.raises(ValueError, match='Invalid plugin ZIP member'):
        await installer.prepare_zip(package(member=name), 'counter.zip', [])
    assert not (installer.records / 'counter.json').exists()


@pytest.mark.asyncio
async def test_zip_rejects_symlink_and_invalid_archive(tmp_path):
    installer = PluginInstaller(tmp_path)
    member = zipfile.ZipInfo('link')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match='Invalid plugin ZIP member'):
        await installer.prepare_zip(package(member=member), 'counter.zip', [])
    with pytest.raises(ValueError, match="ZIP raw=b'not a zip'"):
        await installer.prepare_zip(b'not a zip', 'invalid.zip', [])


@pytest.mark.asyncio
@pytest.mark.parametrize(('original', 'replacement', 'reason'), [
    ('requires_lenbot = ">=0.2,<1"', 'requires_lenbot = ">=99"', 'requires host'),
    ('requires_python = ">=3.13"', 'requires_python = "<3"', 'requires Python'),
    ('platforms = ["linux", "darwin", "win32"]', 'platforms = []', 'platforms'),
    ('version = "1.0.0"', 'version = "latest"', 'Invalid version'),
    ('requires_lenbot = ">=0.2,<1"', 'requires_lenbot = ""', 'Version range must be explicit'),
    ('interface = 1', 'interface = 0', '接口版本'),
])
async def test_zip_compatibility_rejects_before_installation(tmp_path, original, replacement, reason):
    installer = PluginInstaller(tmp_path)
    manifest = (COUNTER / 'plugin.toml').read_text().replace(original, replacement)
    with pytest.raises(ValueError, match=reason):
        await installer.prepare_zip(package(changed_manifest=manifest), 'counter.zip', [])
    assert not (installer.records / 'counter.json').exists()
