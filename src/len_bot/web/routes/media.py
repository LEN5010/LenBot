"""Authenticated curated-media operations; originals remain scope-bound."""
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/media", tags=["media"])


def public_asset(asset):
    return {key: asset[key] for key in ("id", "scope", "source_event_id", "mime_type", "description", "tags", "enabled", "curated", "created_at", "palette_order")}


@router.get("")
async def list_media(request: Request, scene_id: str = "global-safe", query: str = "", curated: bool | None = None,
                     enabled: bool | None = None, palette_only: bool = False, page: int = Query(1,ge=1),
                     page_size: int = Query(48,ge=1,le=100), user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.media_assets(scene_id,query,curated=curated,enabled=enabled,
                                                                     palette_only=palette_only,page=page,page_size=page_size)


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
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return public_asset(asset)


class MediaEditRequest(BaseModel):
    scope: str
    description: str = Field(max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool
    palette_order: int | None = Field(default=None, ge=0)


@router.post("/{asset_id}")
async def edit_media(asset_id: str, req: MediaEditRequest, request: Request, user: str = Depends(get_current_user)):
    try:
        options = {"palette_order": req.palette_order} if "palette_order" in req.model_fields_set else {}
        asset = await request.app.state.runtime.media_service.edit(asset_id, req.scope, req.description, req.tags, req.enabled, **options)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return public_asset(asset)


@router.get("/{asset_id}/file")
async def media_file(asset_id: str, scene_id: str, request: Request, user: str = Depends(get_current_user)):
    try:
        asset, data = await request.app.state.runtime.query_service.media_file(asset_id, scene_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    return Response(content=data, media_type=asset["mime_type"], headers={"Cache-Control": "private, no-store"})
