"""Hold one host child; only an explicit, clean restart launches another."""

from pathlib import Path
import signal
import subprocess
import sys

from .runtime.lifecycle import RESTART_EXIT


def run(*, container: bool = False) -> int:
    root = Path.cwd()
    child: subprocess.Popen | None = None
    stopping = False

    def stop(signum, frame) -> None:
        nonlocal stopping
        stopping = True
        if child is not None:
            child.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == 'win32' else signum)

    signals = (signal.SIGINT, signal.SIGTERM) if sys.platform != 'win32' else (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)
    previous = {sig: signal.signal(sig, stop) for sig in signals}
    try:
        while not stopping:
            # This argument enables lifecycle control only; all settings come from the root file.
            child = subprocess.Popen(
                [sys.executable, '-c', f'from len_bot.next.host import main; main(restartable=True, container={container!r})'],
                cwd=root, start_new_session=sys.platform != 'win32',
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            )
            if stopping:
                child.terminate()
            code = child.wait()
            child = None
            if stopping or code != RESTART_EXIT:
                return code if code >= 0 else 128 - code
            print('LenBot：旧宿主已退出，应用已选插件候选版本。', flush=True)
            child = subprocess.Popen([sys.executable, '-m', 'len_bot.next.maintenance.apply_plugins'],
                                     cwd=root, start_new_session=sys.platform != 'win32',
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0)
            if stopping:
                child.terminate()
            code = child.wait()
            child = None
            if stopping or code != 0:
                return code if code >= 0 else 128 - code
            print('LenBot：按根配置重新启动。', flush=True)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    raise SystemExit(run())


def container_main() -> None:
    raise SystemExit(run(container=True))


if __name__ == '__main__':
    main()
