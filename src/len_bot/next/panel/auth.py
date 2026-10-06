"""Authentication and change notification shared by the isolated panels."""

from __future__ import annotations

import asyncio
import hmac
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from len_bot.web.auth import (
    clear_login_failures, create_session, login_blocked, record_login_failure,
    revoke_session, session_user, verify_password,
)
from ..configuration.maintenance import PanelSettings
from ..configuration.types import STRICT


class Login(BaseModel):
    model_config = STRICT
    username: str
    password: str = Field(repr=False)


def cookie_name(connection: HTTPConnection) -> str:
    port = connection.url.port
    if port is None:
        port = 443 if connection.url.scheme in {"https", "wss"} else 80
    return f"lenbot_test_session_p{port}"


def install_panel_auth(app: FastAPI, settings: PanelSettings, *,
                       on_logout: Callable[[], None]) -> Callable[[Request], str]:
    last_login_at: float | None = None

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path == "/api/auth/login":
            # A missing field otherwise echoes the complete password-bearing body.
            return JSONResponse(status_code=422, content={"detail": [
                {key: item[key] for key in ("type", "loc", "msg")} for item in error.errors()
            ]})
        return await request_validation_exception_handler(request, error)

    def user(request: Request) -> str:
        return session_user(request.cookies.get(cookie_name(request)))

    @app.post("/api/auth/login")
    async def login(item: Login, request: Request, response: Response):
        nonlocal last_login_at
        key = f"isolated:{request.client.host}"
        if login_blocked(key):
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
        record_login_failure(key)
        password_valid = await asyncio.to_thread(verify_password, item.password, settings.password_hash)
        valid = hmac.compare_digest(item.username.encode('utf-8'), settings.username.encode('utf-8')) and password_valid
        if not valid:
            raise HTTPException(401, "Invalid username or password")
        clear_login_failures(key)
        last_login_at = time.time()
        response.set_cookie(cookie_name(request), create_session(item.username), httponly=True, samesite="lax",
                            secure=settings.cookie_secure, max_age=7 * 86400)
        return {"success": True, "username": item.username, "is_default_password": False}

    @app.get("/api/auth/me")
    async def me(current: str = Depends(user)):
        return {"username": current, "is_default_password": False, "last_login_at": last_login_at}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, _: str = Depends(user)):
        name = cookie_name(request)
        revoke_session(request.cookies[name])
        response.delete_cookie(name)
        on_logout()
        return {"success": True}

    return user


async def changes_socket(websocket: WebSocket, listeners: set[asyncio.Event]) -> None:
    token = websocket.cookies.get(cookie_name(websocket))
    try:
        session_user(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    changed = asyncio.Event()
    listeners.add(changed)
    changed.set()

    async def push():
        while True:
            await changed.wait()
            changed.clear()
            session_user(token)
            await websocket.send_json({"type": "changed"})

    pushing = asyncio.create_task(push())
    receiving = asyncio.create_task(websocket.receive())
    try:
        done, _ = await asyncio.wait({pushing, receiving}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = await task
            if task is receiving and result["type"] != "websocket.disconnect":
                await websocket.close(code=1003, reason="This stream only reports changes")
    except HTTPException:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        listeners.remove(changed)
        pushing.cancel()
        receiving.cancel()
        await asyncio.gather(pushing, receiving, return_exceptions=True)
