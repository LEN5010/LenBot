"""Install a built release beside an independent instance; never start the host."""

import argparse
import json
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

BUNDLE = Path(__file__).resolve().parent


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')
    path.chmod(0o700)


def entrypoints(root: Path, platform: str, uv: str) -> None:
    instance = root / 'instance'
    executable(root / 'run', '#!/bin/sh\nset -eu\ncd ' + shlex.quote(str(instance))
               + '\nexec ' + shlex.quote(str(root / 'current/.venv/bin/len-bot')) + '\n')
    # The deployment path selects a process, not an application configuration override.
    path = str(Path(uv).parent) + ':/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    if platform == 'linux':
        def systemd(value: str) -> str:
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        unit = ('[Unit]\nDescription=LenBot user instance\nAfter=network.target\n\n[Service]\nType=simple\n'
                f'ExecStart={systemd(str(root / "run"))}\nWorkingDirectory={systemd(str(instance))}\n'
                f'Environment={systemd("PATH=" + path)}\nRestart=no\nTimeoutStopSec=180\nUMask=0077\n'
                '\n[Install]\nWantedBy=default.target\n')
        (root / 'lenbot.service').write_text(unit)
        service = '''#!/bin/sh
set -eu
case "${1:-}" in
  install)
    mkdir -p "$HOME/.config/systemd/user"
    cp ROOT/lenbot.service "$HOME/.config/systemd/user/lenbot.service"
    systemctl --user daemon-reload ;;
  start|stop|restart|status) systemctl --user "$1" lenbot.service ;;
  *) printf '%s\n' '用法：service install|start|stop|restart|status'; exit 2 ;;
esac
'''.replace('ROOT', shlex.quote(str(root)))
    else:
        plist = {'Label': 'local.lenbot', 'ProgramArguments': [str(root / 'run')],
                 'WorkingDirectory': str(instance), 'RunAtLoad': False, 'KeepAlive': False,
                 'ExitTimeOut': 180, 'Umask': 0o077, 'EnvironmentVariables': {'PATH': path},
                 'StandardOutPath': str(root / 'logs/host.log'),
                 'StandardErrorPath': str(root / 'logs/host.stderr.log')}
        (root / 'local.lenbot.plist').write_bytes(plistlib.dumps(plist))
        service = '''#!/bin/sh
set -eu
DOMAIN="gui/$(id -u)"
case "${1:-}" in
  install) launchctl bootstrap "$DOMAIN" ROOT/local.lenbot.plist ;;
  start) launchctl kickstart "$DOMAIN/local.lenbot" ;;
  stop) launchctl kill SIGTERM "$DOMAIN/local.lenbot" ;;
  restart)
    launchctl bootout "$DOMAIN" ROOT/local.lenbot.plist
    launchctl bootstrap "$DOMAIN" ROOT/local.lenbot.plist
    launchctl kickstart "$DOMAIN/local.lenbot" ;;
  status) launchctl print "$DOMAIN/local.lenbot" ;;
  *) printf '%s\n' '用法：service install|start|stop|restart|status'; exit 2 ;;
esac
'''.replace('ROOT', shlex.quote(str(root)))
        for name, action in (('start', 'start'), ('stop', 'stop'), ('restart', 'restart')):
            executable(root / (name + '.command'), '#!/bin/sh\nset -eu\nexec '
                       + shlex.quote(str(root / 'service')) + ' ' + action + '\n')
    executable(root / 'service', service)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'upgrade'))
    parser.add_argument('root', type=Path, help='Deployment directory; instance lives in its instance/ child')
    args = parser.parse_args()
    metadata = json.loads((BUNDLE / 'release.json').read_text())
    expected = {'linux': 'linux', 'macos': 'darwin'}[metadata['platform']]
    if sys.platform != expected:
        raise ValueError(f'This is the {metadata["platform"]} deployment package; actual platform={sys.platform}')
    root = args.root.expanduser().resolve()
    instance = root / 'instance'
    current = root / 'current'
    uv = shutil.which('uv')
    if uv is None:
        raise FileNotFoundError('uv is not installed in PATH')
    if args.action == 'install':
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        instance.mkdir(mode=0o700)
        (root / 'releases').mkdir()
        (root / 'logs').mkdir(mode=0o700)
    else:
        # Fail before installation if this deployment still owns a running instance.
        run(str(current / '.venv/bin/python'), '-c',
            'from pathlib import Path; from len_bot.next.instance_lock import instance_lock; '
            'lock=instance_lock(Path.cwd()); lock.__enter__(); lock.__exit__(None,None,None)', cwd=instance)
    release = root / 'releases' / metadata['version']
    release.mkdir()
    wheel = BUNDLE / metadata['wheel']
    shutil.copy2(wheel, release / wheel.name)
    shutil.copy2(BUNDLE / 'release.json', release / 'release.json')
    shutil.copy2(BUNDLE / 'requirements.txt', release / 'requirements.txt')
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(BUNDLE / name, release / name)
    run(uv, 'venv', '--python', '3.13', str(release / '.venv'))
    python = str(release / '.venv/bin/python')
    run(uv, 'pip', 'install', '--python', python, '--requirement',
        str(release / 'requirements.txt'), str(release / wheel.name))
    if args.action == 'upgrade':
        if (instance / 'lenbot.config.json').exists():
            for module in ('migrate', 'migrate_memory_jobs', 'plugin_dependencies'):
                run(python, '-m', 'len_bot.next.' + module, cwd=instance)
        else:
            print('实例尚无根配置，仅升级程序；首次配置留到明确运行时创建。')
    # Do not switch a currently running instance, including one started during installation.
    run(python, '-c', '''from pathlib import Path
import os
from len_bot.next.instance_lock import instance_lock
root = Path.cwd().parent
with instance_lock(Path.cwd()):
    pending = root / 'current.new'
    pending.symlink_to(Path('releases') / VERSION)
    os.replace(pending, root / 'current')
'''.replace('VERSION', repr(metadata['version'])), cwd=instance)
    entrypoints(root, metadata['platform'], uv)
    print(f'已安装并选择 {metadata["version"]}，未启动宿主。实例：{instance}')
    print(f'前台首次配置／运行：{shlex.quote(str(root / "run"))}')
    print(f'注册服务但不启动：{shlex.quote(str(root / "service"))} install')
    print('已有服务继续使用固定 run 入口；确认配置与服务就绪后再明确 start。')


if __name__ == '__main__':
    main()
