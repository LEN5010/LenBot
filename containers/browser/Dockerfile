FROM python:3.13-slim

# Only the shared typed protocol and browser entrypoint are copied. No Bot
# configuration, credentials, database, login state or runtime socket enters.
RUN python -m pip install --no-cache-dir playwright==1.62.0 pydantic==2.13.5 \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/playwright python -m playwright install --with-deps chromium \
    && useradd --uid 65532 --create-home --shell /usr/sbin/nologin browser \
    && chmod -R a+rX /opt/playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /opt/lenbot
COPY src/len_bot/__init__.py ./len_bot/__init__.py
COPY src/len_bot/browser ./len_bot/browser
COPY src/len_bot/events/models.py ./len_bot/events/models.py
COPY src/len_bot/execution/protocol.py ./len_bot/execution/protocol.py
COPY src/len_bot/media/segment_protocol.py ./len_bot/media/segment_protocol.py
RUN python -m compileall -q /opt/lenbot/len_bot
ENV PYTHONPATH=/opt/lenbot
WORKDIR /workspace
USER 65532:65532
CMD ["python", "-m", "len_bot.browser.container_worker"]
