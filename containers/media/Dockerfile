FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir pydantic==2.13.5 httpx==0.28.1 \
    && useradd --uid 65532 --create-home --shell /usr/sbin/nologin media
WORKDIR /opt/lenbot
COPY src/len_bot/__init__.py ./len_bot/__init__.py
COPY src/len_bot/events/models.py ./len_bot/events/models.py
COPY src/len_bot/browser/worker.py ./len_bot/browser/worker.py
COPY src/len_bot/execution/protocol.py ./len_bot/execution/protocol.py
COPY src/len_bot/media/segment_protocol.py src/len_bot/media/segment_worker.py ./len_bot/media/
RUN python -m compileall -q /opt/lenbot/len_bot
ENV PYTHONPATH=/opt/lenbot
WORKDIR /workspace
USER 65532:65532
CMD ["python", "-m", "len_bot.media.segment_worker"]
