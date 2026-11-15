"""단일 관리자 세션 로그인 + routine 용 Bearer 토큰."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import get_settings

MAX_FAILS = 5
WINDOW_SEC = 15 * 60
_fails: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def login_blocked(ip: str) -> bool:
    q = _fails[ip]
    now = time.time()
    while q and now - q[0] > WINDOW_SEC:
        q.popleft()
    return len(q) >= MAX_FAILS


def check_credentials(ip: str, username: str, password: str) -> bool:
    s = get_settings()
    ok = hmac.compare_digest(username.encode(), s.admin_username.encode()) and hmac.compare_digest(
        password.encode(), s.admin_password.encode()
    )
    if not ok:
        _fails[ip].append(time.time())
    else:
        _fails.pop(ip, None)
    return ok


class LoginRequired(Exception):
    pass


def require_login(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise LoginRequired()
    return user


def require_token(request: Request) -> None:
    token = get_settings().routine_token
    if not token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ROUTINE_TOKEN 미설정")
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
