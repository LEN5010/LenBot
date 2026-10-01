#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
service="$root/state/services/asr"
cd "$service"
exec "$service/build/bin/whisper-server" \
  --host 127.0.0.1 \
  --port 18171 \
  --model "$service/models/ggml-large-v3-turbo-q5_0.bin" \
  --inference-path /v1/audio/transcriptions \
  --language auto \
  --no-fallback
