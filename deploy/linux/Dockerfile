FROM python:3.13-slim AS build
RUN python -m pip install --no-cache-dir uv==0.12.13
WORKDIR /opt/lenbot
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim
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
