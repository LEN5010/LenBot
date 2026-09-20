"""Authenticated curated-media operations; originals remain scope-bound."""
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt
import httpx

from len_bot.web.auth import get_current_user
from len_bot.media.models import CuratedMediaBaseline, CuratedMediaSavedError, MediaEditConflict, media_purpose

router = APIRouter(prefix="/api/media", tags=["media"])


def public_asset(asset):
    return {**{key: asset[key] for key in ("id", "scope", "source_event_id", "mime_type", "description", "tags", "enabled", "curated", "created_at", "palette_order")},
        'purpose':media_purpose(asset['tags'])}


def saved_error(error: CuratedMediaSavedError):
    return HTTPException(409, {'message':str(error), 'asset_saved':True, 'asset_id':error.asset_id,
        'scope':error.scope, 'event_id':error.event_id, 'phase':error.phase})


@router.get("")
async def list_media(request: Request, scene_id: str = "global-safe", query: str = "", curated: bool | None = None,
                     enabled: bool | None = None, palette_only: bool = False, page: int = Query(1,ge=1),
                     purpose: Literal['character_reference','sticker','media'] | None = None,
                     page_size: int = Query(48,ge=1,le=100), user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.media_assets(scene_id,query,curated=curated,enabled=enabled,
                                                                     palette_only=palette_only,purpose=purpose,page=page,page_size=page_size)


@router.get("/palette")
async def media_palette(request: Request, scene_id: str = "global-safe", user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.media_palette(scene_id)


@router.get("/{asset_id}")
async def media_detail(asset_id: str, scene_id: str, request: Request, user: str = Depends(get_current_user)):
    result=await request.app.state.runtime.query_service.media_asset(asset_id,scene_id)
    if result is None:raise HTTPException(404,"未找到本场景的素材")
    return result


@router.post("")
async def upload_media(request: Request, file: UploadFile = File(...), scope: str = Form("global-safe"),
                       description: str = Form(""), tags: str = Form(""), user: str = Depends(get_current_user)):
    data = await file.read(request.app.state.runtime.config.media_max_image_bytes+1)
    try:
        asset = await request.app.state.runtime.media_service.upload(data, scope, description, list(dict.fromkeys(tags.split())))
    except CuratedMediaSavedError as error:
        raise saved_error(error) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return public_asset(asset)


class MediaEditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    baseline: CuratedMediaBaseline
    scope: str
    description: str = Field(max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool
    palette_order: StrictInt | None = Field(default=None, ge=0)


@router.post("/{asset_id}")
async def edit_media(asset_id: str, req: MediaEditRequest, request: Request, user: str = Depends(get_current_user)):
    try:
        options = {"palette_order": req.palette_order} if "palette_order" in req.model_fields_set else {}
        asset = await request.app.state.runtime.media_service.edit(asset_id, req.scope, req.description, req.tags, req.enabled, baseline=req.baseline, **options)
    except CuratedMediaSavedError as error:
        raise saved_error(error) from error
    except MediaEditConflict as error:
        raise HTTPException(409, {'message':str(error), 'asset_saved':False, 'path':[error.field]}) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return public_asset(asset)


@router.get("/{asset_id}/file")
async def media_file(asset_id: str, scene_id: str, request: Request, user: str = Depends(get_current_user)):
    try:
        asset, data = await request.app.state.runtime.query_service.media_file(asset_id, scene_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    except OSError as error:
        raise HTTPException(404, '已登记媒体的本地文件当前不可读取') from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(502, f'媒体来源返回 HTTP {error.response.status_code}，当前内容不可读取') from error
    except httpx.RequestError as error:
        raise HTTPException(502, '媒体来源请求失败，当前内容不可读取') from error
    return Response(content=data, media_type=asset["mime_type"], headers={"Cache-Control": "private, no-store"})
