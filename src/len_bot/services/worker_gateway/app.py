"""The Gateway's narrow HTTP surface.

Six routes, and each answers one question:

| route | what it answers |
|---|---|
| `POST /v1/executions` | accept this execution; the same id is never started twice |
| `GET /v1/executions/{id}` | where this execution actually is, and whether its container is gone |
| `POST /v1/executions/{id}/cancel` | ask for a stop, and report how far the stop got |
| `GET /v1/executions/{id}/events?after=` | the ordered facts after a sequence, never repeated |
| `GET /v1/executions/{id}/artifacts` | the files this execution produced, by registered id |
| `GET /v1/artifacts/{id}` | one registered file's bytes |

Authentication says the caller is LenBot; it does not say the caller owns this
execution or that the execution may still act.  A cancelled or finished
execution answers reads normally — that is the point of the journal — but it
never starts work again and never accepts a reuse of its identity.

A technical state is reported as exactly that.  Nothing in these responses
claims a work is complete: ``exited`` means a process ended, and the work's own
business result stays in LenBot's ledger.
"""
from __future__ import annotations

import mimetypes
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from len_bot.execution.journal import ExecutionIdentityConflict
from len_bot.execution.protocol import ExecutionRequest
from len_bot.services.worker_gateway.config import GatewayConfig
from len_bot.services.worker_gateway.runner import ExecutionRunner, GatewayRefusal
from len_bot.services.worker_gateway.store import GatewayStore


def create_app(config: GatewayConfig, store: GatewayStore, runner: ExecutionRunner) -> FastAPI:
    app = FastAPI(title='LenBot Worker Gateway', version='0.1.0',
                  docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(authorization: str | None) -> None:
        # Service-to-service only.  The comparison is constant-time and the
        # refusal never says which part of the header was wrong.
        expected = f'Bearer {config.token}'
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail='服务间认证失败')

    @app.post('/v1/executions')
    async def submit(request: ExecutionRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            record, created = await runner.accept(request)
        except GatewayRefusal as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ExecutionIdentityConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return {'record': record.model_dump(mode='json'), 'accepted': created}

    @app.get('/v1/executions/{execution_id}')
    async def fetch(execution_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        record = await store.get_execution(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail='执行不在日志中')
        return record.model_dump(mode='json')

    @app.post('/v1/executions/{execution_id}/cancel')
    async def cancel(execution_id: str, payload: dict | None = None,
                     authorization: str | None = Header(default=None)):
        authorize(authorization)
        reason = str((payload or {}).get('reason') or '未说明原因的取消请求')[:2000]
        try:
            record, requested = await runner.cancel(execution_id, reason)
        except GatewayRefusal as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        # `requested` false means the run had already reached its own end; a
        # stop was neither needed nor carried out, and the answer says so
        # instead of reporting a stop that never happened.
        return {'record': record.model_dump(mode='json'), 'requested': requested,
                'termination': (record.termination.model_dump(mode='json')
                                if record.termination else None)}

    @app.get('/v1/executions/{execution_id}/events')
    async def events(execution_id: str, after: int = Query(default=0, ge=0),
                     authorization: str | None = Header(default=None)):
        authorize(authorization)
        if await store.get_execution(execution_id) is None:
            raise HTTPException(status_code=404, detail='执行不在日志中')
        read = await store.read_execution_events(execution_id, after)
        return {'events': [item.model_dump(mode='json') for item in read]}

    @app.get('/v1/executions/{execution_id}/artifacts')
    async def artifacts(execution_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        record = await store.get_execution(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail='执行不在日志中')
        listed = await store.artifacts_for(execution_id)
        # A registered artifact is one whose bytes were stored, so it is not
        # individually over a limit.  The listing itself can be cut off at the
        # deployment's artifact cap, and that is reported as truncation rather
        # than as a complete directory.
        return {'execution_id': execution_id, 'artifacts': listed,
                'truncated': len(listed) >= config.max_artifacts,
                'workspace_id': record.workspace_id}

    @app.get('/v1/artifacts/{artifact_id}')
    async def artifact_bytes(artifact_id: str, authorization: str | None = Header(default=None),
                             offset: int = Query(default=0, ge=0),
                             limit: int | None = Query(default=None, ge=1, le=1_000_000)):
        """One registered file's bytes, optionally one bounded range of them.

        A range is served as an actual suffix of the stored file: the
        descriptor is positioned before reading, so a caller asking for a page
        of a large artifact does not pay for the whole file.  The length of the
        bytes sent back is what tells the caller whether it reached the end.
        """
        authorize(authorization)
        artifact = await store.artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail='产物未登记')
        record = await store.get_execution(artifact['execution_id'])
        if record is None:
            raise HTTPException(status_code=404, detail='产物所属执行不在日志中')
        # The header itself must stay latin-1 encodable, so a non-ASCII
        # filename (中文等) travels in RFC 5987 ``filename*=`` with a plain
        # ASCII fallback, instead of raising during response encoding.  The
        # headers are built before the descriptor is opened, and a failure to
        # build the response closes the descriptor instead of leaking it.
        media_type = (artifact['media_type'] or mimetypes.guess_type(artifact['path'])[0]
                      or 'application/octet-stream')
        filename = Path(artifact['path']).name
        fallback = filename.encode('ascii', 'ignore').decode().replace('"', '_') or 'artifact'
        disposition = (f'attachment; filename="{fallback}"; '
                       f"filename*=UTF-8''{quote(filename, safe='')}")
        try:
            fd = runner.open_stored_artifact(artifact['execution_id'], artifact_id, offset=offset)
        except GatewayRefusal as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except OSError:
            raise HTTPException(status_code=409, detail='产物不再是可下载的普通文件') from None

        def chunks():
            remaining = limit
            try:
                while remaining is None or remaining > 0:
                    want = 65536 if remaining is None else min(65536, remaining)
                    data = os.read(fd, want)
                    if not data:
                        break
                    if remaining is not None:
                        remaining -= len(data)
                    yield data
            finally:
                os.close(fd)

        try:
            return StreamingResponse(chunks(), media_type=media_type,
                                     headers={'Content-Disposition': disposition})
        except BaseException:
            os.close(fd)
            raise

    @app.on_event('startup')
    async def reconcile():
        # A Gateway may have been restarted while a container it started was
        # still running.  Settling that before serving means an operator's
        # first query already sees where the run really is.
        await runner.sweep()

    @app.on_event('shutdown')
    async def stop_runs():
        await runner.shutdown()

    return app
