$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '请先安装 uv。' }
& uv run --no-project --python 3.13 "$PSScriptRoot/install.py" @args
exit $LASTEXITCODE
