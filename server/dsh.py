import asyncio
import logging
import os
import pwd
import shlex
import re
import signal
import socket
from contextlib import suppress
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.responses import Response, StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .runtime import HOME, ai_env, require

router = APIRouter()
lock = asyncio.Lock()
process = None
reader = None
port = None
origin = None
native_cookie = None
login_process = None
login_port = None
login_token = None
login_deadline = 0
login_reader = None
login_timer = None
LOGIN_TTL = 900
HOP = {"connection", "transfer-encoding", "keep-alive", "upgrade", "proxy-connection", "te", "trailer"}
LOGIN_URL = re.compile(r"https?://[^\s]+[?&]token=[^\s]+")


def browser_origin(request):
    scheme = {"wss": "https", "ws": "http"}.get(request.url.scheme, request.url.scheme)
    return f"{scheme}://{request.headers['host']}"


async def stop():
    async with lock:
        await stop_login()
        await stop_web()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def terminate(child, output):
    if child and child.returncode is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(child.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await child.wait()
    if output:
        output.cancel()
        await asyncio.gather(output, return_exceptions=True)


async def stop_web():
    global process, reader, native_cookie
    child, output = process, reader
    reader = process = native_cookie = None
    await terminate(child, output)


async def stop_login():
    global login_process, login_port, login_token, login_deadline, login_reader, login_timer
    child, output, timer = login_process, login_reader, login_timer
    login_process = login_port = login_token = login_reader = login_timer = None
    login_deadline = 0
    if timer and timer is not asyncio.current_task():
        timer.cancel()
    await terminate(child, output)


async def drain_output(stream, ready):
    try:
        while line := await stream.readline():
            if ready.done():
                continue
            match = LOGIN_URL.search(line.decode(errors="replace"))
            if match:
                url = urlsplit(match[0])
                if token := parse_qs(url.query).get("token", [None])[0]:
                    ready.set_result(token)
    finally:
        if not ready.done():
            ready.set_result(None)


async def launch(web_port, *options):
    try:
        child = await asyncio.create_subprocess_exec(
            require("dsh"), "web", "--host", "127.0.0.1", "--port", str(web_port), "--no-open",
            *options, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=HOME, env=ai_env(), start_new_session=True,
        )
    except OSError:
        raise HTTPException(502, "无法启动 DSH，请在服务器执行 dsh web --help 检查安装") from None
    ready = asyncio.get_running_loop().create_future()
    output = asyncio.create_task(drain_output(child.stdout, ready))
    try:
        token = await asyncio.wait_for(ready, 45)
        if not token:
            raise HTTPException(502, "DSH 启动提前退出，请在服务器运行 dsh web 检查安装与配置")
        return child, output, token
    except asyncio.TimeoutError:
        await terminate(child, output)
        raise HTTPException(504, "DSH 启动超时，请在服务器运行 dsh web 检查安装与配置") from None
    except (HTTPException, asyncio.CancelledError):
        await terminate(child, output)
        raise


async def expire_login(deadline):
    await asyncio.sleep(LOGIN_TTL)
    async with lock:
        if login_deadline == deadline:
            await stop_login()


@router.post("/api/dsh/web")
async def start(request: Request):
    async with lock:
        if process and process.returncode is None and native_cookie:
            return {"url": "/dsh/"}
        return await boot(browser_origin(request))


@router.post("/api/dsh/restart")
async def restart(request: Request):
    async with lock:
        await stop_login()
        return await boot(browser_origin(request))


@router.post("/api/dsh/login")
async def login_helper(request: Request):
    global login_process, login_port, login_token, login_deadline, login_reader, login_timer
    async with lock:
        now = asyncio.get_running_loop().time()
        if not (login_process and login_process.returncode is None and login_token and login_deadline > now):
            await stop_login()
            login_port = free_port()
            login_process, login_reader, login_token = await launch(login_port)
            login_deadline = asyncio.get_running_loop().time() + LOGIN_TTL
            login_timer = asyncio.create_task(expire_login(login_deadline))
        host = request.url.hostname
        user = pwd.getpwuid(os.getuid()).pw_name
        destination = (shlex.quote(f"{user}@{host}")
                       if host and host not in {"localhost", "127.0.0.1", "::1"} else "用户名@服务器地址")
        base = f"http://127.0.0.1:{login_port}/"
        return {
            "url": str(httpx.URL(base, params={"token": login_token})),
            "command": f"ssh -N -L 127.0.0.1:{login_port}:127.0.0.1:{login_port} {destination}",
            "seconds": int(login_deadline - asyncio.get_running_loop().time()) + 1,
        }


@router.post("/api/dsh/login/stop")
async def login_stop():
    async with lock:
        await stop_login()
        return {"ok": True}


async def boot(request_origin):
    global process, reader, port, origin, native_cookie
    await stop_web()
    origin = request_origin
    port = free_port()
    process, reader, token = await launch(port, "--public-url", origin + "/dsh/",
                                          "--trusted-host", urlsplit(origin).netloc)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(base + "/", params={"token": token}, headers={"Origin": base})
        if response.status_code != 303 or not response.cookies:
            raise HTTPException(502, "DSH Web 本机认证失败")
        native_cookie = "; ".join(f"{key}={value}" for key, value in response.cookies.items())
    except httpx.HTTPError:
        await stop_web()
        raise HTTPException(502, "DSH Web 本机认证失败") from None
    except (HTTPException, asyncio.CancelledError):
        await stop_web()
        raise
    return {"url": "/dsh/"}


def upstream_headers(request, websocket=False):
    exclude = HOP | {"host", "origin", "referer", "cookie", "authorization", "accept-encoding",
                     "content-length", "x-server-console"}
    headers = [(key, value) for key, value in request.headers.items()
               if key not in exclude and not key.startswith(("sec-websocket-", "x-forwarded-"))]
    base = f"http://127.0.0.1:{port}"
    headers.append(("cookie", native_cookie))
    if not websocket:
        headers.append(("origin", base))
    return headers


@router.api_route("/dsh/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(request: Request, path: str):
    if not process or process.returncode is not None or not native_cookie:
        raise HTTPException(503, "请先在 AI 工作台启动 DSH Web")
    raw_path = request.scope["raw_path"].decode("ascii").removeprefix("/dsh")
    url = httpx.URL(f"http://127.0.0.1:{port}").copy_with(raw_path=raw_path.encode() + (
        b"?" + request.scope["query_string"] if request.scope["query_string"] else b""
    ))
    client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), trust_env=False)
    content = request.stream() if request.method not in ("GET", "HEAD") else None
    try:
        upstream = await client.send(client.build_request(
            request.method, url, headers=upstream_headers(request), content=content,
        ), stream=True)
    except ClientDisconnect:
        await client.aclose()
        return Response(status_code=499)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "无法连接 DSH Web") from None
    except asyncio.CancelledError:
        await client.aclose()
        raise

    async def release():
        await upstream.aclose()
        await client.aclose()

    response = StreamingResponse(upstream.aiter_raw(), upstream.status_code, background=BackgroundTask(release))
    for key, value in upstream.headers.multi_items():
        if key in HOP or key == "set-cookie":
            continue
        if key == "location":
            location = urlsplit(value)
            if location.netloc in {"", urlsplit(origin).netloc, f"127.0.0.1:{port}"} and location.path.startswith("/"):
                path = location.path if location.path.startswith("/dsh/") else "/dsh" + location.path
                value = location._replace(scheme="", netloc="", path=path).geturl()
        response.raw_headers.append((key.encode("ascii"), value.encode("latin-1")))
    return response


@router.websocket("/dsh/{path:path}")
async def ws_proxy(ws: WebSocket, path: str):
    if not process or process.returncode is not None or not native_cookie:
        await ws.close()
        return
    target = f"ws://127.0.0.1:{port}" + ws.scope["raw_path"].decode("ascii").removeprefix("/dsh")
    if ws.url.query:
        target += "?" + ws.url.query
    headers = dict(upstream_headers(ws, websocket=True))
    try:
        async with connect(target, origin=f"http://127.0.0.1:{port}", additional_headers=headers, proxy=None,
                           subprotocols=ws.scope["subprotocols"] or None, max_size=16 * 1024 * 1024) as upstream:
            await ws.accept(subprotocol=upstream.subprotocol)

            async def from_browser():
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    await upstream.send(msg["text"] if "text" in msg else msg["bytes"])

            async def from_dsh():
                async for msg in upstream:
                    if isinstance(msg, str):
                        await ws.send_text(msg)
                    else:
                        await ws.send_bytes(msg)

            tasks = [asyncio.create_task(from_browser()), asyncio.create_task(from_dsh())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, WebSocketException) as error:
        logging.getLogger(__name__).warning("DSH WebSocket: %s", error)
    with suppress(RuntimeError, WebSocketDisconnect):
        await ws.close()
