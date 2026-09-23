# The control panel is built here, not committed.  Vite content-hashes every
# asset, so a checked-in bundle turns each frontend change into a delete+add
# churn of ~40 files and buries the real diff.  web/app.py serves whatever
# lands in web/static/dist; this stage is what puts it there for a deploy.
FROM node:22-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS frontend
WORKDIR /build/src/len_bot/web/frontend
COPY src/len_bot/web/frontend/package.json src/len_bot/web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/len_bot/web/frontend/ ./
# vite.config.js writes to ../static/dist, so this lands at
# /build/src/len_bot/web/static/dist and keeps the repository layout.
RUN npm run build

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS build
RUN python -m pip install --no-cache-dir uv==0.12.13
WORKDIR /opt/lenbot
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# The panel must exist before the wheel is built; uv_build packages it as
# module data.  Frontend sources are excluded from the wheel in pyproject.toml.
COPY --from=frontend /build/src/len_bot/web/static/dist ./src/len_bot/web/static/dist
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10000 lenbot \
    && useradd --uid 10000 --gid 10000 --no-create-home --shell /usr/sbin/nologin lenbot \
    && mkdir -p /srv/lenbot /var/lib/lenbot
COPY --from=build /opt/lenbot/.venv /opt/lenbot/.venv
# This is the deployed project root. ConfigStore reads and atomically saves
# lenbot.config.json here; its whole parent directory is mounted read/write.
WORKDIR /srv/lenbot
USER 10000:10000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/lenbot/.venv/bin/len-bot"]
