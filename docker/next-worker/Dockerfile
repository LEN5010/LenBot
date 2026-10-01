FROM node:24-bookworm-slim

# Task image only. The host supplies the one task's generated Pi settings in
# its writable home and task-only tokens at read-only /run/lenbot. No host
# credential directory is copied or mounted.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl ffmpeg fonts-noto-cjk git jq \
       pandoc python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/uv \
    && /opt/uv/bin/pip install --no-cache-dir uv==0.12.13 \
    && npm install --global --ignore-scripts @earendil-works/pi-coding-agent@0.87.1

RUN npm install --prefix /opt/lenbot/browser --ignore-scripts --save-exact playwright-core@1.62.0 \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/lenbot/browser-binaries \
       node /opt/lenbot/browser/node_modules/playwright-core/cli.js install --with-deps --no-shell chromium \
    && ln -s "$(PLAYWRIGHT_BROWSERS_PATH=/opt/lenbot/browser-binaries \
       node -e 'process.stdout.write(require("/opt/lenbot/browser/node_modules/playwright-core").chromium.executablePath())')" \
       /usr/local/bin/lenbot-chromium \
    && mkdir -p /opt/lenbot/browser-home \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10000 agent \
    && useradd --uid 10000 --gid 10000 --home-dir /home/agent --create-home agent \
    && mkdir -p /workspace /run/lenbot \
    && chown 10000:10000 /workspace /run/lenbot

COPY src/len_bot/next/worker_bridge.py /opt/lenbot/worker_bridge.py
COPY src/len_bot/next/egress_wire.py /opt/lenbot/egress_wire.py
COPY src/len_bot/next/worker_egress_bridge.py /opt/lenbot/worker_egress_bridge.py
COPY docker/next-worker/lenbot-extension.ts /opt/lenbot/lenbot-extension.ts
COPY --chmod=755 docker/next-worker/lenbot-browser.cjs /usr/local/bin/lenbot-browser

COPY scripts/collect_python_licenses.py /opt/lenbot/collect_python_licenses.py
COPY scripts/collect_frontend_licenses.cjs /opt/lenbot/collect_node_licenses.cjs
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/lenbot/licenses/project/
COPY licenses/ /usr/share/lenbot/licenses/project/third-party/
RUN /opt/uv/bin/python /opt/lenbot/collect_python_licenses.py /usr/share/lenbot/licenses/python \
    && node /opt/lenbot/collect_node_licenses.cjs "$(npm root --global)" /usr/share/lenbot/licenses/node-global \
    && node /opt/lenbot/collect_node_licenses.cjs /opt/lenbot/browser/node_modules /usr/share/lenbot/licenses/browser

ENV PATH="/opt/uv/bin:${PATH}" HOME=/home/agent
WORKDIR /workspace
USER 10000:10000
CMD ["sleep", "infinity"]
