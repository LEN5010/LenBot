import logging
from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from len_bot.web.auth import get_current_user
from len_bot.web.query_service import RuntimeQueryService
from len_bot.web.log_ring import LogRingBuffer
from len_bot.web.routes.auth import router as auth_router
from len_bot.web.routes.overview import router as overview_router
from len_bot.web.routes.websocket import router as websocket_router
from len_bot.web.routes.models import router as models_router
from len_bot.web.routes.settings import router as settings_router
from len_bot.web.routes.plugins import router as plugins_router
from len_bot.web.routes.cockpit import router as cockpit_router
from len_bot.web.routes.replay import router as replay_router
from len_bot.web.routes.voice import router as voice_router
from len_bot.web.routes.media import router as media_router

def create_app(runtime, cors_origins: list[str] | None = None) -> FastAPI:
    app = FastAPI(
        title="Len Bot Control Plane",
        version="0.3.0",
        description="Operational Cockpit for Persistent Social Agent"
    )

    # Attach runtime + read facade (ADR-0022: routes only read via the service)
    app.state.runtime = runtime
    runtime.query_service = RuntimeQueryService(runtime)

    log_ring = LogRingBuffer(capacity=1000)
    logging.getLogger("len_bot").addHandler(log_ring)
    logging.getLogger("len_bot").setLevel(logging.INFO)
    app.state.log_ring = log_ring

    # Security (§三十九): no wildcard-origin + credentials combination. Local
    # control plane defaults to same-origin; extra origins are opt-in config.
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include API routers
    app.include_router(auth_router)
    app.include_router(overview_router)
    app.include_router(websocket_router)
    app.include_router(models_router)
    app.include_router(settings_router)
    app.include_router(plugins_router)
    app.include_router(cockpit_router)
    app.include_router(replay_router)
    app.include_router(voice_router)
    app.include_router(media_router)

    @app.get("/api/logs")
    async def get_logs(level: str | None = None, limit: int = 200, user: str = Depends(get_current_user)):
        return app.state.log_ring.snapshot(level=level, limit=limit)

    # Static assets: Vue build output takes precedence over the legacy page
    dist_dir = Path(__file__).parent / "static" / "dist"
    if dist_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(dist_dir / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # The SPA shell is served publicly (login screen lives in it);
            # every /api route remains auth-gated.
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "接口不存在，请重启 LenBot 后再试"})
            candidate = dist_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")

    else:
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            index_file = Path(__file__).parent / "static" / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            return {"message": "Len Bot Control Plane Backend Running"}

    return app
