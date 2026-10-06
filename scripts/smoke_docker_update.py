"""Exercise image replacement and recovery against an isolated loopback registry and temporary volumes."""

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.request

from smoke_install import free_port, request, check_panel, wait_until_served


def run(artifacts: Path, work: Path, old_image: str, host_image: str, updater_image: str) -> dict:
    work.mkdir(parents=True, exist_ok=False)
    project = 'lenbot-update-smoke-' + secrets.token_hex(6)
    network = project + '-fixture'
    registry_name, release_name = project + '-registry', project + '-releases'
    registry_port, panel_port, update_port = free_port(), free_port(), free_port()
    prefix = f'127.0.0.1:{registry_port}/lenbot'
    manifest = json.loads((artifacts / 'release-manifest.json').read_text())
    version = manifest['version']
    docker = lambda *args, **kwargs: subprocess.run(['docker', *args], check=True, **kwargs)
    deployment = work / 'deployment'
    deployment.mkdir()
    compose = ['docker', 'compose', '-p', project, '-f', str(deployment / 'host.compose.yaml'),
               '-f', str(deployment / 'host.version.compose.yaml'), '-f', str(deployment / 'host.updater.compose.yaml')]
    current_host = project + '-host'
    try:
        docker('network', 'create', network)
        docker('run', '-d', '--name', registry_name, '-p', f'127.0.0.1:{registry_port}:5000', 'registry:2')
        opener = urllib.request.build_opener()
        wait_until_served(opener, f'http://127.0.0.1:{registry_port}/v2/', time.monotonic() + 30, lambda: True)
        for source, target in ((old_image, prefix + ':0.0.0'), (host_image, prefix + ':' + version)):
            docker('tag', source, target)
            docker('push', target)
        docker('run', '--rm', '--mount', f'type=bind,source={deployment},target=/deployment',
               '--mount', 'type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock',
               '--entrypoint', 'python', updater_image, '/opt/lenbot-updater/init.py',
               '--version', '0.0.0', '--project', project, '--panel-port', str(panel_port), '--update-port', str(update_port))
        # Only this newly created deployment is redirected to the local synthetic release source.
        script = '''import json; from pathlib import Path
p=Path('/deployment/deployment.json'); v=json.loads(p.read_text()); v['release_api']='http://releases:8080/releases'; v['registry']='dockerhub'; p.write_text(json.dumps(v))
p=Path('/deployment/current.json'); v=json.loads(p.read_text()); v['host_image']=OLD; p.write_text(json.dumps(v))
'''.replace('OLD', repr(prefix + ':0.0.0'))
        docker('run', '--rm', '--mount', f'type=bind,source={deployment},target=/deployment', '--entrypoint', 'python', updater_image, '-c', script)
        recipe = (deployment / 'host.compose.yaml').read_text().replace('ghcr.io/lendevs/lenbot:0.0.0', prefix + ':0.0.0')
        recipe += f'networks:\n  default:\n    external: true\n    name: {network}\n'
        (deployment / 'host.compose.yaml').write_text(recipe)
        helper = json.loads((deployment / 'host.updater.compose.yaml').read_text())
        helper['services']['lenbot-updater']['image'] = updater_image
        (deployment / 'host.updater.compose.yaml').write_text(json.dumps(helper))
        releases = work / 'releases'
        releases.mkdir()
        manifest['images'] = {'dockerhub': {'host': prefix + ':' + version, 'worker': prefix + ':' + version,
                                           'updater': updater_image}}
        (releases / 'release-manifest.json').write_text(json.dumps(manifest))
        (releases / 'releases').write_text(json.dumps([{'tag_name': manifest['tag'], 'draft': False,
            'prerelease': manifest['prerelease'], 'body': 'Synthetic Docker update fixture.',
            'assets': [{'name': 'release-manifest.json', 'browser_download_url': 'http://releases:8080/release-manifest.json'}]}]))
        docker('run', '-d', '--name', release_name, '--network', network, '--network-alias', 'releases',
               '--mount', f'type=bind,source={releases},target=/srv,readonly', '--workdir', '/srv',
               '--entrypoint', 'python', updater_image, '-m', 'http.server', '8080', '--bind', '0.0.0.0')
        subprocess.run([*compose, 'up', '-d'], check=True)
        panel = f'http://127.0.0.1:{panel_port}'
        wait_until_served(opener, panel + '/', time.monotonic() + 60, lambda: True)
        logs = subprocess.run([*compose, 'logs', 'lenbot'], capture_output=True, text=True, check=True).stdout
        import re
        match = re.search(r'/#token=(\S+)', logs)
        assert match, logs
        code, body = request(opener, panel + '/api/setup', headers={'X-Setup-Token': match[1]}, body={
            'bot_id': 'onebot:90001', 'owners': ['onebot:70001'], 'timezone': 'Asia/Shanghai', 'delivery': 'simulated',
            'onebot': {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:19999', 'access_token': 'fixture'},
            'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:19998/v1', 'api_key': 'fixture'},
            'mind': {'provider': 'primary', 'model': 'fixture', 'context_window_tokens': 8192},
            'compaction': {'input_tokens': 6000}, 'scene': 'onebot:group:80001', 'persona_id': 'smoke',
            'persona_name': 'Docker 检查角色', 'brief': '临时实例', 'voice_text': '简短', 'boundaries': '',
            'panel_port': 11307, 'username': 'smoke', 'password': 'smoke-password'})
        assert code == 200, body
        check_panel(panel_port, 'smoke', 'smoke-password', lambda: True)
        import http.cookiejar
        auth = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        code, text = request(auth, panel + '/api/auth/login', body={'username': 'smoke', 'password': 'smoke-password'})
        assert code == 200, text
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            code, text = request(auth, panel + '/api/host/updates/session', body={})
            if code == 200:
                break
            time.sleep(0.25)
        assert code == 200, text
        token = json.loads(text)['url'].split('#session=')[1]
        headers = {'Authorization': 'Bearer ' + token}
        controller = f'http://127.0.0.1:{update_port}'
        def status():
            code, text = request(opener, controller + '/api/status', headers=headers)
            assert code == 200, text
            return json.loads(text)
        def finish(expected):
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                value = status()
                if value['status'] == expected:
                    return value
                if value['status'] == 'failed':
                    _, log = request(opener, controller + '/api/log', headers=headers)
                    raise RuntimeError(value['error'] + '\n' + log)
                time.sleep(0.5)
            raise TimeoutError(status())
        def action(name, payload):
            code, text = request(opener, controller + '/api/' + name, headers=headers, body=payload)
            assert code == 202, text
        docker('exec', current_host, '/opt/lenbot/.venv/bin/python', '-c',
               'from pathlib import Path; Path("upgrade-fixture.txt").write_text("before")')
        action('prepare', {'tag': manifest['tag']})
        finish('prepared')
        action('apply', {})
        assert finish('complete')['current_version'] == version
        docker('exec', current_host, '/opt/lenbot/.venv/bin/python', '-c',
               'from pathlib import Path; Path("upgrade-fixture.txt").write_text("after")')
        action('restore', {})
        assert finish('restored')['current_version'] == '0.0.0'
        action('start', {})
        finish('complete')
        value = docker('exec', current_host, '/opt/lenbot/.venv/bin/python', '-c',
                       'from pathlib import Path; print(Path("upgrade-fixture.txt").read_text())', capture_output=True, text=True).stdout.strip()
        assert value == 'before', value
        check_panel(panel_port, 'smoke', 'smoke-password', lambda: True)
        return {'baseline': '0.0.0', 'target': version, 'upgrade': 'ready', 'restore': 'program, volume selection and data restored'}
    finally:
        # Only names belonging to this invocation are removed. No daemon-wide cleanup.
        containers = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'], capture_output=True, text=True, check=True).stdout.split()
        owned = [name for name in containers if name.startswith(project + '-')]
        if owned:
            for name in owned:
                with (work / (name + '.log')).open('w') as log:
                    subprocess.run(['docker', 'logs', name], stdout=log, stderr=subprocess.STDOUT, check=True)
            subprocess.run(['docker', 'rm', '-f', *owned], check=True)
        volumes = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, check=True).stdout.split()
        owned_volumes = [name for name in volumes if name.startswith(project + '-')]
        if owned_volumes:
            subprocess.run(['docker', 'volume', 'rm', *owned_volumes], check=True)
        subprocess.run(['docker', 'network', 'rm', network], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('work', type=Path)
    parser.add_argument('--baseline-image', required=True)
    parser.add_argument('--host-image', required=True)
    parser.add_argument('--updater-image', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.artifacts.resolve(), args.work.resolve(), args.baseline_image, args.host_image, args.updater_image), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
