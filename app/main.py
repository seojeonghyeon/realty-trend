from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequired
from app.config import get_settings
from app.db import init_db
from app.routers import api_routine, pages

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="부동산 동향", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class OriginCheck(BaseHTTPMiddleware):
    """세션 쿠키로 인증되는 폼 POST 에 대해 교차 출처 요청 차단 (간단한 CSRF 방어)."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and not request.url.path.startswith("/api/"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                o = urlparse(origin)
                allowed = {
                    request.headers.get("x-forwarded-host", ""),
                    request.headers.get("host", ""),
                    urlparse(settings.public_base_url).netloc,  # 리버스 프록시가 Host 를 바꿔도 허용
                } - {""}
                if not o.netloc or o.netloc not in allowed:  # 'Origin: null' 도 차단
                    return PlainTextResponse("cross-origin request blocked", status_code=403)
        return await call_next(request)


app.add_middleware(OriginCheck)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.cookie_secure,
    same_site="lax",
    max_age=60 * 60 * 24 * 30,
    path=settings.root_path or "/",
    session_cookie="realty_session",  # 같은 도메인의 다른 서비스(time-page)와 쿠키 분리
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_routine.router)
app.include_router(pages.router)


@app.exception_handler(LoginRequired)
async def _login_redirect(request: Request, exc: LoginRequired):
    return RedirectResponse(f"{settings.root_path}/login?next={quote(request.url.path)}", status_code=303)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
