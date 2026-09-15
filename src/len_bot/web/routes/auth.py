import asyncio

from fastapi import APIRouter, Request, Response, Depends, HTTPException, status
from pydantic import BaseModel
from len_bot.web.auth import (
    clear_login_failures, create_session, get_current_user, hash_password,
    login_blocked, record_login_failure, revoke_session, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    runtime = request.app.state.runtime
    event_store = runtime.event_store
    throttle_key = f"{request.client.host if request.client else 'unknown'}:{req.username}"
    if login_blocked(throttle_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="登录尝试过于频繁，请稍后再试")

    # 1. Look up user or initialize default admin
    user = await event_store.get_dashboard_user(req.username)
    if not user:
        if req.username == runtime.config.dashboard_default_admin_user:
            default_hash = await asyncio.to_thread(
                hash_password, runtime.config.dashboard_default_admin_password)
            await event_store.create_dashboard_user(req.username, default_hash)
            user = await event_store.get_dashboard_user(req.username)
        else:
            record_login_failure(throttle_key)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    # 2. Verify password.  PBKDF2 runs off the event loop: one login must not
    # stall the websocket and every other request for its hashing time.
    if not user or not await asyncio.to_thread(verify_password, req.password, user["password_hash"]):
        record_login_failure(throttle_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    clear_login_failures(throttle_key)

    # 3. Update last login & create session
    await event_store.update_dashboard_user_login(req.username)
    token = create_session(req.username)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=runtime.config.dashboard_cookie_secure,
        max_age=7 * 86400
    )

    is_default_password = await asyncio.to_thread(
        verify_password, runtime.config.dashboard_default_admin_password, user["password_hash"])
    return {
        "success": True,
        "username": req.username,
        "is_default_password": is_default_password
    }

@router.post("/logout")
async def logout(request: Request, response: Response, current_user: str = Depends(get_current_user)):
    token = request.cookies.get("session_token")
    if token:
        revoke_session(token)
    response.delete_cookie("session_token")
    return {"success": True}

@router.get("/me")
async def me(request: Request, current_user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    user = await runtime.event_store.get_dashboard_user(current_user)
    is_default = False
    if user:
        is_default = await asyncio.to_thread(
            verify_password, runtime.config.dashboard_default_admin_password, user["password_hash"])
    return {
        "username": current_user,
        "is_default_password": is_default,
        "last_login_at": user.get("last_login_at") if user else None
    }

@router.post("/change_password")
async def change_password(req: ChangePasswordRequest, request: Request, current_user: str = Depends(get_current_user)):
    runtime = request.app.state.runtime
    event_store = runtime.event_store
    user = await event_store.get_dashboard_user(current_user)
    if not user or not await asyncio.to_thread(verify_password, req.current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password incorrect")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 6 characters")

    new_hash = await asyncio.to_thread(hash_password, req.new_password)
    await event_store.update_dashboard_user_password(current_user, new_hash)
    return {"success": True, "message": "Password updated successfully"}
