"""Authenticated inspection and explicit processing of saved scene audio."""
import json

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from ...media.audio import TranscribeArguments
from .settings import _body


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    refresh: bool


def register_host_audio(app: FastAPI, *, runtime, user) -> None:
    service = runtime.audio

    def check(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置此场景")

    @app.get('/api/host/scenes/{scene}/audio')
    async def listing(scene: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                      _: str = Depends(user)):
        check(scene)
        rows = service.records.items(scene, limit + 1, offset)
        config = service.configs[scene]
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None,
                "automatic": config.transcribe_audio, "timezone": config.timezone,
                "limits": config.audio.model_dump(mode="json"),
                "onebot_ws_frame_bytes": None if config.onebot is None else config.onebot.max_frame_bytes,
                "available": config.models.roles.asr is not None and service.platform is not None,
                "worker_error": service.errors.get(scene), "stopping": service.closing}

    @app.get('/api/host/scenes/{scene}/audio/{message}/{audio_index}/wav')
    async def original(scene: str, message: str, audio_index: int = Path(ge=1), _: str = Depends(user)):
        check(scene)
        item = service.records.item(scene, message, audio_index)
        if item is None or item['wav'] is None:
            raise HTTPException(404, "本场景尚未保存此语音原件")
        return Response(item['wav'], media_type='audio/wav', headers={'Cache-Control': 'no-store'})

    @app.get('/api/host/scenes/{scene}/audio/{message}/{audio_index}/calls')
    async def calls(scene: str, message: str, audio_index: int = Path(ge=1), _: str = Depends(user)):
        check(scene)
        if service.records.item(scene, message, audio_index) is None:
            raise HTTPException(404, "本场景没有此语音处理记录")
        return service.records.calls(scene, message, audio_index)

    @app.post('/api/host/scenes/{scene}/audio/{message}/{audio_index}/transcribe')
    async def transcribe(scene: str, message: str, request: Request,
                         audio_index: int = Path(ge=1), _: str = Depends(user)):
        check(scene)
        if not runtime.accepting or service.closing:
            raise HTTPException(409, "宿主正在停止")
        change = await _body(request, Refresh)
        item = service.records.item(scene, message, audio_index)
        if item is not None and item['status'] in {'queued', 'running'}:
            raise HTTPException(409, "此语音正在排队或处理中，不重复提交")
        try:
            text = await service.transcribe(scene, TranscribeArguments(message=message, audio=audio_index,
                                                                        refresh=change.refresh))
        except Exception as error:
            raise HTTPException(422, f'{type(error).__name__}: {error}') from error
        return json.loads(text.splitlines()[0])
