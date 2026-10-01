"""Shared built frontend; each host registers only its own business routes."""

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def mount_panel(app: FastAPI, *, mode: Literal["isolated", "isolated-multi"],
                home: str, assets_dir: Path | None = None) -> None:
    @app.get("/api/panel-context")
    async def panel_context():
        return {"mode": mode, "home": home}

    dist = Path(__file__).parent / "static" / "dist" if assets_dir is None else assets_dir
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/")
    async def index():
        entry = dist / "index.html"
        if not entry.is_file():
            return JSONResponse(status_code=503, content={"detail": "控制面板尚未构建，请先在 frontend 目录执行 npm ci 和 npm run build"})
        return FileResponse(entry, headers={"Cache-Control": "no-cache"})

    @app.get("/{full_path:path}")
    async def unknown_path(full_path: str):
        return JSONResponse(status_code=404, content={"detail": "接口不存在" if full_path.startswith("api/") else "资源不存在"})
