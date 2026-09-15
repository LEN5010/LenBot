import hashlib
import hmac
import secrets
import time
from typing import Optional
from fastapi import Request, HTTPException, Depends, status

# In-memory active session tokens: token -> {"username": str, "expires_at": float}
_ACTIVE_SESSIONS: dict[str, dict] = {}
SESSION_TTL = 7 * 86400.0  # 7 days

# Failed-login throttle: "client:username" -> recent failure timestamps.  The
# dashboard port may be reachable beyond localhost, and PBKDF2 alone does not
# stop an online guessing loop.
_FAILED_LOGINS: dict[str, list[float]] = {}
LOGIN_ATTEMPT_WINDOW = 900.0
LOGIN_ATTEMPT_LIMIT = 5


def login_blocked(key: str) -> bool:
    now = time.time()
    attempts = [moment for moment in _FAILED_LOGINS.get(key, []) if now - moment < LOGIN_ATTEMPT_WINDOW]
    if attempts:
        _FAILED_LOGINS[key] = attempts
    else:
        _FAILED_LOGINS.pop(key, None)
    return len(attempts) >= LOGIN_ATTEMPT_LIMIT


def record_login_failure(key: str) -> None:
    now = time.time()
    if len(_FAILED_LOGINS) > 10000:
        for stale in [name for name, moments in _FAILED_LOGINS.items()
                      if not moments or now - moments[-1] >= LOGIN_ATTEMPT_WINDOW]:
            _FAILED_LOGINS.pop(stale, None)
    attempts = [moment for moment in _FAILED_LOGINS.get(key, []) if now - moment < LOGIN_ATTEMPT_WINDOW]
    attempts.append(now)
    _FAILED_LOGINS[key] = attempts


def clear_login_failures(key: str) -> None:
    _FAILED_LOGINS.pop(key, None)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """PBKDF2-HMAC-SHA256 password hasher with random salt."""
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a password against the stored salt$hash."""
    try:
        salt, expected_hex = stored_hash.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
        return hmac.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False

def create_session(username: str) -> str:
    """Creates a new session token for the user."""
    token = secrets.token_urlsafe(32)
    _ACTIVE_SESSIONS[token] = {
        "username": username,
        "expires_at": time.time() + SESSION_TTL
    }
    return token

def revoke_session(token: str) -> None:
    """Revokes a session token on logout."""
    _ACTIVE_SESSIONS.pop(token, None)

async def get_current_user(request: Request) -> str:
    """FastAPI dependency to extract and validate the authenticated session (Cookie-only, ADR-0031)."""
    token = request.cookies.get("session_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )

    session = _ACTIVE_SESSIONS.get(token)
    if not session or session["expires_at"] < time.time():
        _ACTIVE_SESSIONS.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid"
        )

    return session["username"]
