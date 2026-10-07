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
    executable(root / 'run', '#!/bin/sh\nset -eu\nexec '
               + shlex.quote(str(root / 'control/.venv/bin/python')) + ' '
               + shlex.quote(str(root / 'control/code/controller.py')) + ' ' + shlex.quote(str(root)) + '\n')
    # The deployment path selects a process, not an application configuration override.
    path = str(Path(uv).parent) + ':/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    if platform == 'windows':
        quote_ps = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = '& ' + quote_ps(root / 'control/.venv/Scripts/python.exe') + ' -X utf8 ' + quote_ps(root / 'control/code/controller.py') + ' ' + quote_ps(root)
        (root / 'run.ps1').write_text(command + '\n', encoding='utf-8-sig')
        (root / 'run.cmd').write_text('@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"\n')
        # Starts at logon through Task Scheduler; stop asks the controller, which stops the host before exiting.
        (root / 'service.ps1').write_text('''param([Parameter(Mandatory)][ValidateSet('install', 'uninstall', 'start', 'stop', 'status')][string]$Action)
$ErrorActionPreference = 'Stop'
$Root = ROOT
$Name = 'LenBot'
switch ($Action) {
  'install' {
    $run = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $Root 'run.ps1') + '"')
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $run -Trigger $logon -Settings $settings -Force | Out-Null
  }
  'uninstall' { Unregister-ScheduledTask -TaskName $Name -Confirm:$false }
  'start' { Start-ScheduledTask -TaskName $Name }
  'stop' {
    $control = Get-Content -Raw -Encoding UTF8 (Join-Path $Root 'instance/.runtime/update-control.json') | ConvertFrom-Json
    Invoke-RestMethod -Method Post -Uri ($control.endpoint + '/api/shutdown') -Headers @{ Authorization = 'Bearer ' + $control.token } -ContentType 'application/json' -Body '{}' | Out-Null
  }
  'status' { Get-ScheduledTask -TaskName $Name | Get-ScheduledTaskInfo }
}
'''.replace('ROOT', quote_ps(root)), encoding='utf-8-sig')
        return
    if platform == 'linux':
        def systemd(value: str) -> str:
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        # KillMode=mixed: only the controller gets SIGTERM, so it can let a running update step finish and stop the host itself.
        unit = ('[Unit]\nDescription=LenBot user instance\nAfter=network.target\n\n[Service]\nType=simple\n'
                f'ExecStart={systemd(str(root / "run"))}\nWorkingDirectory={systemd(str(instance))}\n'
                f'Environment={systemd("PATH=" + path)}\nRestart=no\nKillMode=mixed\nTimeoutStopSec=240\nUMask=0077\n'
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
                 'ExitTimeOut': 240, 'Umask': 0o077, 'EnvironmentVariables': {'PATH': path},
                 # Only the updater process's own console output; the host's records are in the instance's lenbot.jsonl.
                 'StandardOutPath': str(root / 'logs/service.stdout.log'),
                 'StandardErrorPath': str(root / 'logs/service.stderr.log')}
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
    if sys.platform == 'win32':
        # A redirected Windows console uses a legacy code page that cannot print the Chinese messages.
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'prepare', 'upgrade'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    metadata = json.loads((BUNDLE / 'release.json').read_text(encoding='utf-8'))
    expected = {'linux': 'linux', 'macos': 'darwin', 'windows': 'win32'}[metadata['platform']]
    if sys.platform != expected:
        raise ValueError(f'This is the {metadata["platform"]} package; actual platform={sys.platform}')
    root = args.root.expanduser().resolve()
    uv = shutil.which('uv')
    if uv is None:
        raise FileNotFoundError('uv is not installed in PATH')
    if args.action == 'install':
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        (root / 'instance').mkdir(mode=0o700)
        (root / 'releases').mkdir()
        (root / 'logs').mkdir(mode=0o700)
        (root / 'control').mkdir()
        shutil.copytree(BUNDLE / 'deploy/updater', root / 'control/code')
        run(uv, 'venv', '--python', metadata['python'], str(root / 'control/.venv'))
        (root / 'deployment.json').write_text(json.dumps({
            'mode': 'native', 'platform': metadata['platform'], 'uv': uv,
            'release_api': 'https://api.github.com/repos/lendevs/LenBot/releases?per_page=100',
        }, indent=2) + '\n', encoding='utf-8')
    state_file = root / 'updates/state.json'
    if args.action == 'upgrade' and state_file.exists():
        state = json.loads(state_file.read_text(encoding='utf-8'))
        if state['status'] == 'failed' and state.get('snapshot_complete'):
            raise ValueError(f'上次升级失败且尚未恢复，快照在 {state["snapshot"]}；先运行 run 打开恢复页恢复，不能覆盖恢复记录')
    release = root / 'releases' / metadata['version']
    # A version that was prepared, or restored away from, leaves its environment; rebuild it unless it is active.
    if args.action in ('prepare', 'upgrade') and release.exists():
        current = json.loads((root / 'current.json').read_text(encoding='utf-8'))
        if current['version'] == metadata['version']:
            raise ValueError('Cannot rebuild the environment of the active release')
        shutil.rmtree(release)
    release.mkdir()
    for name in (metadata['wheel'], 'release.json', 'requirements.txt', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(BUNDLE / name, release / name)
    run(uv, 'venv', '--python', metadata['python'], str(release / '.venv'))
    python = str(release / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'))
    run(uv, 'pip', 'install', '--python', python, '--requirement', str(release / 'requirements.txt'), str(release / metadata['wheel']))
    sys.path.insert(0, str(root / 'control/code'))
    from common import write_json
    if args.action == 'upgrade':
        from native import Native
        import time
        import secrets
        with (root / 'logs/offline-upgrade.log').open('a', encoding='utf-8') as log:
            backend = Native(root, log)
            if (root / 'instance/lenbot.config.json').exists():
                check = backend.inspect(metadata)
                if check['blocked_plugins']:
                    raise ValueError('\n'.join(check['blocked_plugins']))
                # Same names and record as the panel update, so the controller's recovery page covers both.
                snapshot = root / 'backups' / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4))
                old = backend.metadata()
                backend.backup(snapshot)
                record = {'old': old, 'target': metadata, 'snapshot': str(snapshot), 'snapshot_complete': True,
                          'stopped': True, 'offline': True}
                write_json(state_file, {'status': 'applying', 'stage': 'migrate', **record})
                try:
                    backend.migrate(metadata)
                except Exception as error:
                    write_json(state_file, {'status': 'failed', 'stage': 'migrate', **record,
                                            'error': f'{type(error).__name__}: {error}'})
                    raise
                write_json(state_file, {'status': 'complete', 'stage': 'complete', **record, 'stopped': False})
            else:
                write_json(state_file, {'status': 'idle', 'stage': 'installed'})
        # The controller never replaces its own code; an offline upgrade is the explicit way to move it forward.
        fresh, retired = root / 'control/code.new', root / 'control/code.old'
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
        shutil.copytree(BUNDLE / 'deploy/updater', fresh)
        (root / 'control/code').rename(retired)
        fresh.rename(root / 'control/code')
        shutil.rmtree(retired)
    if args.action != 'prepare':
        write_json(root / 'current.json', metadata)
        entrypoints(root, metadata['platform'], uv)
    print(f'已准备 {metadata["version"]}，未启动宿主。实例：{root / "instance"}')
    print(f'启动：{root / ("run.cmd" if sys.platform == "win32" else "run")}')


if __name__ == '__main__':
    main()
