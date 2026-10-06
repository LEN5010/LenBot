ARG PACKAGE_SOURCE=source

# The control panel is built here, not committed.  Vite content-hashes every
# asset, so a checked-in bundle turns each frontend change into a delete+add
# churn of ~40 files and buries the real diff.  web/shell.py serves whatever
# lands in web/static/dist; this stage is what puts it there for a deploy.
FROM node:22-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS frontend
WORKDIR /build/src/len_bot/web/frontend
COPY src/len_bot/web/frontend/package.json src/len_bot/web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/len_bot/web/frontend/ ./
COPY scripts/collect_frontend_licenses.cjs /build/scripts/collect_frontend_licenses.cjs
# vite.config.js writes to ../static/dist, so this lands at
# /build/src/len_bot/web/static/dist and keeps the repository layout.
RUN npm run build
RUN mkdir -p /build/licenses && cp -r ../static/dist/assets/licenses/frontend /build/licenses/frontend

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS source
RUN python -m pip install --no-cache-dir uv==0.12.13
WORKDIR /opt/lenbot
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY scripts/collect_python_licenses.py /build/collect_python_licenses.py
COPY src ./src
# The panel must exist before the wheel is built; uv_build packages it as
# module data.  Frontend sources are excluded from the wheel in pyproject.toml.
COPY --from=frontend /build/src/len_bot/web/static/dist ./src/len_bot/web/static/dist
RUN uv sync --locked --no-dev --no-editable
RUN .venv/bin/python /build/collect_python_licenses.py /build/licenses/python
RUN python /build/collect_python_licenses.py /build/licenses/install-tools
COPY --from=frontend /build/licenses/frontend /build/licenses/frontend
RUN mkdir /build/licenses/project && cp LICENSE NOTICE THIRD_PARTY_NOTICES.md /build/licenses/project/

# Release builds consume the same wheel and dependency selection as the platform bundles.
FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS wheel
RUN python -m pip install --no-cache-dir uv==0.12.13
WORKDIR /opt/lenbot
COPY --from=release *.whl requirements.txt /wheels/
COPY scripts/collect_python_licenses.py /build/collect_python_licenses.py
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /build/licenses/project/
RUN uv venv .venv && uv pip install --python .venv/bin/python --requirement /wheels/requirements.txt /wheels/*.whl
RUN .venv/bin/python /build/collect_python_licenses.py /build/licenses/python \
    && python /build/collect_python_licenses.py /build/licenses/install-tools \
    && .venv/bin/python -c "import shutil; from pathlib import Path; import len_bot; shutil.copytree(Path(len_bot.__file__).parent / 'web/static/dist/assets/licenses/frontend', '/build/licenses/frontend')"

FROM ${PACKAGE_SOURCE} AS distribution

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata util-linux git openssh-client docker-cli \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10000 lenbot \
    && useradd --uid 10000 --gid 10000 --home-dir /srv/lenbot --no-create-home --shell /usr/sbin/nologin lenbot \
    && install -d -o 10000 -g 10000 /srv/lenbot /var/lib/lenbot
# A release-specific named volume at this path retains plugin packages when
# the container is recreated. Docker populates a new volume from this image.
COPY --from=distribution --chown=10000:10000 /opt/lenbot/.venv /opt/lenbot/.venv
COPY --from=distribution /usr/local/bin/uv /usr/local/bin/uv
COPY --from=distribution /build/licenses /usr/share/lenbot/licenses
# This is the deployed instance root. The panel reads and atomically saves
# lenbot.config.json here; its whole parent directory is mounted read/write.
WORKDIR /srv/lenbot
USER 10000:10000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/lenbot/.venv/bin/lenbot-container"]
