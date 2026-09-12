FROM python:3.13-slim

# Dependencies are installed when building the image. Work executions stay offline.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir numpy pandas matplotlib pillow

WORKDIR /workspace
USER 65532:65532
