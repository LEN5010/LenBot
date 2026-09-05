"""Authenticated curated-media operations; originals remain scope-bound."""
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field

from len_bot.web.auth import get_current_user
from len_bot.media.service import MAX_IMAGE_BYTES
from len_bot.tools.retrieval import RetrievalToolkit

router = APIRouter(prefix="/api/media", tags=["media"])


def public_asset(asset):
    return {key: asset[key] for key in ("id", "scope", "source_event_id", "mime_type", "description", "tags", "enabled", "curated", "created_at")}


@router.get("")
async def list_media(request: Request, scene_id: str = "global-safe", query: str = "", user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.media_assets(scene_id, query)


@router.post("")
async def upload_media(request: Request, file: UploadFile = File(...), scope: str = Form("global-safe"),
                       description: str = Form(""), tags: str = Form(""), user: str = Depends(get_current_user)):
    data = await file.read(MAX_IMAGE_BYTES+1)
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


@router.post("/{asset_id}")
async def edit_media(asset_id: str, req: MediaEditRequest, request: Request, user: str = Depends(get_current_user)):
    try:
        asset = await request.app.state.runtime.media_service.edit(asset_id, req.scope, req.description, req.tags, req.enabled)
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


class InspectRequest(BaseModel):
    scene_id: str
    question: str = Field(default="描述图片中可确认的信息", max_length=2000)


@router.post("/{asset_id}/inspect")
async def inspect_media(asset_id: str, req: InspectRequest, request: Request, user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    toolkit = RetrievalToolkit(runtime.event_store, [req.scene_id, "global-safe"], req.scene_id,
        media_service=runtime.media_service, on_observation=runtime.commit_tool_observation)
    result = await toolkit.execute_result("inspect_image", {"asset_id": asset_id, "question": req.question})
    return result.model_dump()
