import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from len_bot.web.routes.auth import router as auth_router
from len_bot.web.routes.overview import router as overview_router
from len_bot.web.routes.websocket import router as websocket_router
from len_bot.web.routes.models import router as models_router
from len_bot.web.routes.settings import router as settings_router
from len_bot.web.routes.plugins import router as plugins_router

def create_app(runtime) -> FastAPI:
    app = FastAPI(
        title="Len Bot Management Dashboard",
        version="0.2.0",
        description="Embedded Administrative Console for Persistent Social Agent"
    )

    # Attach runtime to app state
    app.state.runtime = runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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

    # Static assets directory
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"message": "Len Bot Dashboard Backend Running"}

    return app
