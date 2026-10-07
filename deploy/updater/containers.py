"""Replace one managed host container while preserving its daemon-side mounts."""

import http.client
import copy
import json
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import quote
import urllib.error
import urllib.request

from common import owned_like_parent, read_json, write_json


class Engine(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__('localhost', timeout=190)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Containers:
    def __init__(self, root: Path, log):
        self.root, self.log = root, log
        self.deployment = read_json(root / 'deployment.json')
        self.instance = Path('/srv/lenbot')

    def api(self, method: str, endpoint: str, payload=None):
        connection = Engine(self.deployment['docker_socket'])
        try:
            body = None if payload is None else json.dumps(payload).encode()
            connection.request(method, endpoint, body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if response.status >= 400:
                raise RuntimeError(f'Docker {method} {endpoint}: {response.status}: {data.decode()}')
            return None if not data else json.loads(data)
        finally:
            connection.close()

    def metadata(self) -> dict:
        return read_json(self.root / 'current.json')

    def info(self, container: str) -> dict:
        return self.api('GET', '/containers/' + quote(container, safe='') + '/json')

    def command(self, args: list[str], *, capture: bool = False) -> str:
        result = subprocess.run(['docker', '--host', 'unix://' + self.deployment['docker_socket'], *args],
                                stdout=subprocess.PIPE if capture else self.log, stderr=self.log, check=True, text=True)
        return result.stdout if capture else ''

    def maintenance(self, action: str, *, target: dict | None = None, snapshot: Path | None = None, capture: bool = False) -> str:
        value = self.metadata() if target is None else target
        args = ['run', '--rm', '--volumes-from', value['container'] + (':ro' if action == 'inspect' else ''),
                '--entrypoint', '/opt/lenbot/.venv/bin/python', '--workdir', '/srv/lenbot']
        if action in ('backup', 'restore'):
            args += ['--user', '0:0']
        host = self.info(value['container'])
        for group in host['HostConfig']['GroupAdd'] or []:
            args += ['--group-add', group]
        if snapshot is not None:
            backups = Path(self.deployment['host_directory']) / 'backups'
            args += ['--mount', f'type=bind,source={backups},target=/lenbot-backups']
        args += [value['host_image'], '-m', 'len_bot.next.maintenance.upgrade', action]
        if action == 'inspect':
            args += ['--version', value['version']]
        if snapshot is not None:
            args += ['--snapshot', '/lenbot-backups/' + snapshot.name]
        return self.command(args, capture=capture)

    def prepare(self, manifest: dict, archive: Path | None, work: Path) -> dict:
        images = manifest['images'][self.deployment['registry']]
        host_image, worker_image = images['host'], images['worker']
        self.command(['pull', host_image])
        original = self.info(self.metadata()['container'])
        image_config = self.api('GET', '/images/' + quote(host_image, safe='') + '/json')['Config']
        old_image = self.api('GET', '/images/' + quote(original['Image'], safe='') + '/json')['Config']
        configuration = copy.deepcopy(original['Config'])
        for field in ('Entrypoint', 'Cmd', 'User', 'WorkingDir'):
            if configuration.get(field) == old_image.get(field):
                configuration[field] = image_config.get(field)
        labels = {key: value for key, value in (configuration.get('Labels') or {}).items()
                  if (old_image.get('Labels') or {}).get(key) != value}
        configuration['Labels'] = {**(image_config.get('Labels') or {}), **labels}
        old_env = dict(item.split('=', 1) for item in original['Config'].get('Env') or [])
        defaults = dict(item.split('=', 1) for item in old_image.get('Env') or [])
        overrides = {key: value for key, value in old_env.items() if defaults.get(key) != value}
        new_env = dict(item.split('=', 1) for item in image_config.get('Env') or [])
        configuration['Env'] = [f'{key}={value}' for key, value in {**new_env, **overrides}.items()]
        configuration['Image'] = host_image
        configuration['NetworkingConfig'] = {'EndpointsConfig': {
            name: {key: settings[key] for key in ('IPAMConfig', 'Links', 'Aliases', 'DriverOpts')}
            for name, settings in original['NetworkSettings']['Networks'].items()}}
        host_config = copy.deepcopy(original['HostConfig'])
        volume = self.deployment['project'] + '-python-' + manifest['version']
        # Engines report compose volumes under Mounts or only under Binds; both lists may be absent.
        for mount in host_config.get('Mounts') or []:
            if mount['Target'] == '/opt/lenbot/.venv':
                mount['Source'] = volume
        for index, bind in enumerate(host_config['Binds'] or []):
            if ':/opt/lenbot/.venv:' in bind or bind.endswith(':/opt/lenbot/.venv'):
                host_config['Binds'][index] = volume + bind[bind.index(':'):]
        candidate = self.deployment['project'] + '-candidate-' + work.name.removeprefix('candidate-')
        configuration['HostConfig'] = host_config
        created = self.api('POST', '/containers/create?name=' + quote(candidate), configuration)
        return {'version': manifest['version'], 'host_image': host_image, 'worker_image': worker_image,
                'python_volume': volume, 'container': created['Id']}

    def inspect(self, target: dict) -> dict:
        result = json.loads(self.maintenance('inspect', target=target, capture=True))
        target['work_enabled'] = result['work_enabled']
        if result['work_enabled']:
            self.command(['pull', target['worker_image']])
        return result

    def discard(self, target: dict) -> None:
        if target['container'] != self.metadata()['container']:
            try:
                self.api('DELETE', '/containers/' + quote(target['container']))
            except RuntimeError as error:
                print(error, file=self.log)

    def stop(self) -> None:
        container = self.metadata()['container']
        if self.info(container)['State']['Running']:
            self.api('POST', '/containers/' + quote(container) + '/stop?t=180')

    def backup(self, snapshot: Path) -> None:
        # The daemon must see the same host directory; only its backup child is mounted in maintenance containers.
        snapshot.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.maintenance('backup', snapshot=snapshot)

    def migrate(self, target: dict) -> None:
        self.maintenance('apply', target=target)
        if target['work_enabled']:
            text = 'import json; from pathlib import Path; p=Path("lenbot.config.json"); v=json.loads(p.read_text()); v["worker"]["image"]=IMAGE; p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\\n")'
            self.command(['run', '--rm', '--volumes-from', target['container'], '--workdir', '/srv/lenbot',
                          '--entrypoint', '/opt/lenbot/.venv/bin/python', target['host_image'], '-c',
                          text.replace('IMAGE', repr(target['worker_image']))])

    def select(self, target: dict) -> None:
        previous = self.metadata()
        current = self.info(previous['container'])['Id']
        previous['container'] = current
        selected_id = self.info(target['container'])['Id']
        name = self.deployment['container']
        write_json(self.root / 'current.json', previous)
        if current != selected_id:
            write_json(self.root / 'previous.json', previous)
            if self.info(current)['Name'].lstrip('/') == name:
                old_name = self.deployment['project'] + '-previous-' + previous['version']
                self.api('POST', '/containers/' + quote(current) + '/rename?name=' + quote(old_name))
            if self.info(selected_id)['Name'].lstrip('/') != name:
                self.api('POST', '/containers/' + quote(selected_id) + '/rename?name=' + quote(name))
        selected = {**target, 'container': selected_id}
        write_json(self.root / 'current.json', selected)
        self.write_version(selected)

    def write_version(self, selected: dict) -> None:
        path = self.root / 'host.version.compose.yaml'
        path.write_text(f'services:\n  lenbot:\n    image: {selected["host_image"]}\nvolumes:\n  python:\n'
                        f'    name: {selected["python_volume"]}\n', encoding='utf-8')
        owned_like_parent(path)

    def start(self) -> None:
        self.api('POST', '/containers/' + quote(self.metadata()['container']) + '/start')

    def restore(self, old: dict, snapshot: Path) -> None:
        self.stop()
        previous = old
        if (self.root / 'previous.json').exists():
            recorded = read_json(self.root / 'previous.json')
            if recorded['version'] == old['version']:
                previous = recorded
        self.maintenance('restore', target=previous, snapshot=snapshot)
        current = self.metadata()['container']
        previous = {**previous, 'container': self.info(previous['container'])['Id']}
        write_json(self.root / 'current.json', previous)
        self.write_version(previous)
        if current != previous['container']:
            self.api('DELETE', '/containers/' + quote(current))
        if self.info(previous['container'])['Name'].lstrip('/') != self.deployment['container']:
            self.api('POST', '/containers/' + quote(previous['container']) + '/rename?name=' + quote(self.deployment['container']))

    def publish_reference(self, reference: dict) -> None:
        code = 'import json; from pathlib import Path; p=Path(".runtime/update-control.json"); p.parent.mkdir(exist_ok=True); p.write_text(DATA); p.chmod(0o600)'
        current = self.metadata()
        self.command(['run', '--rm', '--volumes-from', current['container'], '--workdir', '/srv/lenbot',
                      '--entrypoint', '/opt/lenbot/.venv/bin/python', current['host_image'], '-c',
                      code.replace('DATA', repr(json.dumps(reference)))])

    def ready(self, target: dict) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            info = self.info(self.metadata()['container'])
            if not info['State']['Running']:
                raise RuntimeError(f'New container exited: {info["State"]}')
            try:
                with urllib.request.urlopen(self.deployment['panel_url'] + '/api/host/ready', timeout=2) as response:
                    status = json.load(response)
                if status['version'] != target['version']:
                    raise ValueError('Container panel is serving a different release version')
                if status['ready']:
                    return
                if status['status'] == 'failed':
                    raise RuntimeError('Container runtime initialization failed')
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                pass
            time.sleep(0.25)
        raise TimeoutError('New container did not become ready within 90 seconds')
