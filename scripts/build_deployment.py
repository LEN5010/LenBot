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
            guide = (bundle / 'README.md').read_text()
            (bundle / 'README.md').write_text(guide.replace('](../current/', '](deploy/current/').replace(
                '](../current)', '](deploy/current)'))
            shutil.copy2(wheel, bundle / wheel.name)
            shutil.copy2(requirements, bundle / 'requirements.txt')
            shutil.copytree(project / 'deploy', bundle / 'deploy', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(project / 'developer', bundle / 'developer')
            shutil.copytree(project / 'examples', bundle / 'examples', ignore=shutil.ignore_patterns(
                'lenbot.config.json', 'chat.sqlite3*', 'runs'))
            shutil.copytree(project / '.github', bundle / '.github')
            for resource in ('CONTRIBUTING.md', 'CONTRIBUTING.en.md', 'AGENTS.md', 'SECURITY.md'):
                shutil.copy2(project / resource, bundle / resource)
            # Keep the published guides' relative links without bundling the whole source tree.
            for pattern in ('src/len_bot/next/plugins/plugin_catalog.json', 'src/len_bot/prompts/*.md'):
                for source in project.glob(pattern):
                    target = bundle / source.relative_to(project)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
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
