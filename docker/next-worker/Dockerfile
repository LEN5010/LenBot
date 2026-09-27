FROM node:24-bookworm-slim

# Task image only. The host supplies the one task's generated Pi settings in
# its writable home and task-only tokens at read-only /run/lenbot. No host
# credential directory is copied or mounted.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates chromium curl ffmpeg fonts-noto-cjk git jq \
       pandoc python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/uv \
    && /opt/uv/bin/pip install --no-cache-dir uv==0.12.13 \
    && npm install --global --ignore-scripts @earendil-works/pi-coding-agent@0.87.1

RUN groupadd --gid 10000 agent \
    && useradd --uid 10000 --gid 10000 --home-dir /home/agent --create-home agent \
    && mkdir -p /workspace /run/lenbot \
    && chown 10000:10000 /workspace /run/lenbot

COPY src/len_bot/next/worker_bridge.py /opt/lenbot/worker_bridge.py
COPY docker/next-worker/lenbot-extension.ts /opt/lenbot/lenbot-extension.ts

ENV PATH="/opt/uv/bin:${PATH}" HOME=/home/agent
WORKDIR /workspace
USER 10000:10000
CMD ["sleep", "infinity"]
