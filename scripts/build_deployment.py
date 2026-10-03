"""Package the already built wheel with platform installation entry points."""

import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser


def build_deployments(wheel: Path, project: Path, output: Path, requirements: Path) -> list[Path]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_path, = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        version = metadata['Version']
    results = []
    for platform in ('linux', 'macos'):
        name = f'lenbot-{version}-{platform}'
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / name
            shutil.copytree(project / 'deploy/package', bundle, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copy2(wheel, bundle / wheel.name)
            shutil.copy2(requirements, bundle / 'requirements.txt')
            shutil.copytree(project / 'deploy', bundle / 'deploy', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(project / 'developer', bundle / 'developer')
            shutil.copy2(project / 'CONTRIBUTING.md', bundle / 'CONTRIBUTING.md')
            for resource in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
                shutil.copy2(project / resource, bundle / resource)
            (bundle / 'release.json').write_text(json.dumps({
                'version': version, 'platform': platform, 'wheel': wheel.name,
                'python': '3.13', 'dependencies': 'requirements.txt exported from the release uv.lock; uv downloads Python and selected dependencies',
            }, ensure_ascii=False, indent=2) + '\n')
            destination = output / (name + '.tar.gz')
            with tarfile.open(destination, 'x:gz') as archive:
                archive.add(bundle, arcname=name)
            results.append(destination)
    return results
