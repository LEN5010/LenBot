"""Panel commands using the running installation's Python and instance root."""

from pathlib import Path
import shlex
import sys


def maintenance_command(root: Path, module: str) -> str:
    if sys.platform == 'win32':
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        return f'Set-Location -LiteralPath {quote(root)}; & {quote(sys.executable)} -m {module}'
    return f'cd {shlex.quote(str(root))} && {shlex.join([sys.executable, "-m", module])}'
