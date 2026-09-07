from fastapi import APIRouter, Request, Depends, Query
from len_bot.web.auth import get_current_user

router = APIRouter(prefix="/api/overview", tags=["overview"])

@router.get("/stats")
async def get_overview_stats(request: Request, user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.overview()

@router.get("/recent_events")
async def get_recent_events(request: Request, limit: int = Query(25,ge=1,le=100), user: str = Depends(get_current_user)):
    return await request.app.state.runtime.query_service.query_events(limit=limit)


@router.get("/status")
async def runtime_status(request: Request, user: str = Depends(get_current_user)):
    return request.app.state.runtime.query_service.status()
