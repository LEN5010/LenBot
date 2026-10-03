"""Hold one host child; only an explicit, clean restart launches another."""

from pathlib import Path
import signal
import subprocess
import sys

from .host_lifecycle import RESTART_EXIT


def run() -> int:
    root = Path.cwd()
    child: subprocess.Popen | None = None
    stopping = False

    def stop(signum, frame) -> None:
        nonlocal stopping
        stopping = True
        if child is not None:
            child.send_signal(signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        while not stopping:
            # This argument enables lifecycle control only; all settings come from the root file.
            child = subprocess.Popen(
                [sys.executable, '-c', 'from len_bot.next.host import main; main(restartable=True)'],
                cwd=root, start_new_session=True,
            )
            if stopping:
                child.terminate()
            code = child.wait()
            child = None
            if stopping or code != RESTART_EXIT:
                return code if code >= 0 else 128 - code
            print('LenBot：旧宿主已退出，按根配置重新启动。', flush=True)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    raise SystemExit(run())


if __name__ == '__main__':
    main()
