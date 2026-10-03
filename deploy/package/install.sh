#!/bin/sh
set -eu
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
command -v uv >/dev/null || { printf '%s\n' '请先安装 uv，再执行本脚本。'; exit 1; }
exec uv run --no-project --python 3.13 "$HERE/install.py" "$@"
