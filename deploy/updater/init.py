"""Create a new Docker deployment recipe, or switch an existing one to another updater image; never starts anything."""

import argparse
import json
from pathlib import Path
import re
import socket
import subprocess

from common import repository, version_key, write_json


def switch_updater(root: Path, version: str) -> None:
    deployment = json.loads((root / 'deployment.json').read_text(encoding='utf-8'))
    current = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    image = repository(current['host_image']) + '-updater:' + version
    path = root / 'host.updater.compose.yaml'
    recipe = json.loads(path.read_text(encoding='utf-8'))
    recipe['services']['lenbot-updater']['image'] = image
    path.write_text(json.dumps(recipe, indent=2) + '\n')
    print(f'更新器镜像已改为 {image}，宿主未改动。使用以下命令换上新的更新器：')
    print(f'docker compose -p {deployment["project"]} -f host.updater.compose.yaml up -d')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--host-directory', help='Absolute deployment directory as seen by the Docker daemon; normally read from this container mount')
    parser.add_argument('--project', default='lenbot')
    parser.add_argument('--registry', choices=('ghcr', 'dockerhub'), default='ghcr')
    parser.add_argument('--namespace', default='lendevs')
    parser.add_argument('--panel-port', type=int, default=11307)
    parser.add_argument('--update-port', type=int, default=11308)
    parser.add_argument('--updater-only', action='store_true',
                        help='In an existing deployment, only point host.updater.compose.yaml at this version')
    args = parser.parse_args()
    version_key(args.version)
    if args.updater_only:
        switch_updater(Path.cwd(), args.version)
        return
    if re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.project) is None:
        parser.error('project uses lowercase letters, numbers, hyphens and underscores')
    if args.host_directory is None:
        own = json.loads(subprocess.run(['docker', 'inspect', socket.gethostname()], check=True, capture_output=True, text=True).stdout)[0]
        args.host_directory, = [item['Source'] for item in own['Mounts'] if item['Destination'] == '/deployment']
    if not Path(args.host_directory).is_absolute():
        parser.error('host-directory must be absolute')
    root = Path.cwd()
    if (root / 'deployment.json').exists():
        raise FileExistsError('deployment.json already exists; this command initializes only a new deployment')
    prefix = ('ghcr.io/' if args.registry == 'ghcr' else 'docker.io/') + args.namespace + '/lenbot'
    image = prefix + ':' + args.version
    python_volume = args.project + '-python-' + args.version
    data_volume = args.project + '-data'
    for volume in (data_volume, python_volume):
        subprocess.run(['docker', 'volume', 'create', volume], check=True)
    source = Path(__file__).with_name('host.compose.yaml')
    recipe = source.read_text().replace('lenbot-current:local', image).replace('lenbot-data', data_volume).replace('lenbot-python-r1', python_volume)
    recipe = recipe.replace('    image:', f'    container_name: {args.project}-host\n    image:', 1)
    recipe = recipe.replace('127.0.0.1:11307:11307', f'127.0.0.1:{args.panel_port}:11307')
    (root / 'host.compose.yaml').write_text(recipe)
    (root / 'host.version.compose.yaml').write_text('services:\n  lenbot: {}\n')
    updater = {'services': {'lenbot-updater': {
        'image': prefix + '-updater:' + args.version, 'restart': 'no', 'stop_grace_period': '240s',
        'ports': [f'127.0.0.1:{args.update_port}:11308'],
        'volumes': [{'type': 'bind', 'source': args.host_directory, 'target': '/deployment'},
                    {'type': 'bind', 'source': '/var/run/docker.sock', 'target': '/var/run/docker.sock'}]}}}
    # JSON is a YAML subset and retains daemon-side path strings on every client OS.
    (root / 'host.updater.compose.yaml').write_text(json.dumps(updater, indent=2) + '\n')
    write_json(root / 'deployment.json', {
        'mode': 'docker', 'project': args.project, 'container': args.project + '-host',
        'docker_socket': '/var/run/docker.sock', 'host_directory': args.host_directory,
        'registry': args.registry,
        'release_api': 'https://api.github.com/repos/lendevs/LenBot/releases?per_page=100',
        'listen': {'host': '0.0.0.0', 'port': 11308}, 'endpoint': 'http://lenbot-updater:11308',
        'public_url': f'http://127.0.0.1:{args.update_port}', 'panel_url': 'http://lenbot:11307',
    })
    write_json(root / 'current.json', {'version': args.version, 'container': args.project + '-host',
        'host_image': image, 'worker_image': prefix + '-worker:' + args.version, 'python_volume': python_volume})
    (root / 'backups').mkdir(mode=0o700)
    print('已创建配方和新卷，未启动。使用以下命令显式启动：')
    print(f'docker compose -p {args.project} -f host.compose.yaml -f host.version.compose.yaml -f host.updater.compose.yaml up -d')
    print('后台任务另按任务挂载配方与根配置启用；更新器从根配置判断是否需要同步任务镜像。')


if __name__ == '__main__':
    main()
