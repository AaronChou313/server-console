import asyncio
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import docker, dsh, host, mihomo, mihomo_broker, ssh, terminal
from .runtime import ROOT, STATE, binary, config, private_dir, verify_password

COOKIE = "sc_session"
TTL = 7 * 24 * 3600
sessions = {}
attempts = {}
DIST = ROOT / "frontend/dist"


def authenticated(connection):
    return sessions.get(connection.cookies.get(COOKIE), 0) > time.monotonic()


def same_origin(connection):
    supplied = connection.headers.get("origin")
    return supplied == dsh.browser_origin(connection)


@asynccontextmanager
async def lifespan(_):
    await mihomo.restore()
    yield
    await asyncio.gather(dsh.stop(), mihomo.stop())


app = FastAPI(title="Server Console", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class WebAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind, path = scope["type"], scope.get("path", "")
        if kind == "websocket":
            connection = Request({**scope, "type": "http"})
            if not authenticated(connection) or not same_origin(connection):
                await send({"type": "websocket.close", "code": 4401})
                return
        elif kind == "http" and path.startswith(("/api/", "/dsh/")):
            request = Request(scope)
            message = None
            status = 403
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("origin") and not same_origin(request):
                    message = "请求来源无效"
                elif path.startswith("/api/") and request.headers.get("x-server-console") != "1":
                    message = "缺少请求标识"
            if not message and path not in ("/api/auth", "/api/login") and not authenticated(request):
                message, status = "请先登录", 401
            if message:
                await JSONResponse({"detail": message}, status_code=status)(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(WebAccess)
for module in [host, terminal, docker, ssh, dsh, mihomo]:
    app.include_router(module.router)


class Login(BaseModel):
    password: str = Field(max_length=256)


@app.get("/api/auth")
def auth(request: Request):
    return {"authenticated": authenticated(request), "configured": bool(config().get("password_hash"))}


@app.post("/api/login")
async def login(request: Request, form: Login):
    now = time.monotonic()
    for token, expires in list(sessions.items()):
        if expires <= now:
            sessions.pop(token, None)
    for ip, (_, expires) in list(attempts.items()):
        if expires <= now:
            attempts.pop(ip, None)
    ip = request.client.host
    count, until = attempts.get(ip, (0, now + 60))
    if count >= 8:
        raise HTTPException(429, "尝试次数过多，请一分钟后再试")
    attempts[ip] = (count + 1, until)
    if not await asyncio.to_thread(verify_password, form.password):
        raise HTTPException(401, "管理员密码错误")
    attempts.pop(ip, None)
    sessions.pop(request.cookies.get(COOKIE), None)
    token = secrets.token_urlsafe(32)
    sessions[token] = now + TTL
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")
    return response


@app.post("/api/logout")
def logout(request: Request):
    sessions.pop(request.cookies.get(COOKIE), None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/api/tools")
def tools():
    return {**{name: bool(binary(name)) for name in ("zellij", "codex", "dsh", "docker", "tailscale")},
            "mihomo": bool(mihomo.executable() or mihomo_broker.socket_path())}


@app.post("/api/uploads")
async def upload(request: Request):
    name = request.query_params.get("name", "attachment")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120].lstrip(".") or "attachment"
    path = private_dir(STATE / "uploads") / f"{secrets.token_hex(6)}-{name}"
    total = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 25 * 1024 * 1024:
                    raise HTTPException(413, "单个文件最大 25 MiB")
                file.write(chunk)
        if not total:
            raise HTTPException(400, "文件为空")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"path": str(path.resolve())}


@app.get("/{path:path}")
def frontend(path: str):
    if path in {"api", "ws", "dsh"} or path.startswith(("api/", "ws/", "dsh/")):
        raise HTTPException(404)
    actual = (DIST / path).resolve()
    if actual.is_relative_to(DIST) and actual.is_file():
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else None
        return FileResponse(actual, headers=headers)
    if not (DIST / "index.html").exists():
        raise HTTPException(503, "请先构建前端：npm ci --prefix frontend && npm run build --prefix frontend")
    return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
