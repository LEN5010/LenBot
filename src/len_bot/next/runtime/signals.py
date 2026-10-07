"""Install the process stop signals on POSIX and Windows event loops."""

import asyncio
import signal
import sys
from collections.abc import Callable


def install_stop(callback: Callable[[], None]) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    if sys.platform == 'win32':
        signals = (*signals, signal.SIGBREAK)
        previous = {sig: signal.signal(sig, lambda number, frame: loop.call_soon_threadsafe(callback)) for sig in signals}
        def remove() -> None:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    else:
        for sig in signals:
            loop.add_signal_handler(sig, callback)
        def remove() -> None:
            for sig in signals:
                loop.remove_signal_handler(sig)
    return remove
