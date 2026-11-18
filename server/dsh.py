import asyncio
import logging
import os
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
launch_token = None
native_cookie = None
HOP = {"connection", "transfer-encoding", "keep-alive", "upgrade", "proxy-connection", "te", "trailer"}
LOGIN_URL = re.compile(r"https?://[^\s]+[?&]token=[^\s]+")


def browser_origin(request):
    scheme = {"wss": "https", "ws": "http"}.get(request.url.scheme, request.url.scheme)
    return f"{scheme}://{request.headers['host']}"


async def stop():
    global process, reader, native_cookie
    if process and process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
    if reader:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    reader = process = native_cookie = None


async def stdout():
    global launch_token
    while line := await process.stdout.readline():
        match = LOGIN_URL.search(line.decode(errors="replace"))
        if match:
            url = urlsplit(match[0])
            launch_token = parse_qs(url.query).get("token", [None])[0]


@router.post("/api/dsh/web")
async def start(request: Request):
    global process, reader, port, origin, launch_token, native_cookie
    async with lock:
        if not process or process.returncode is not None:
            await stop()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            origin = browser_origin(request)
            launch_token = None
            process = await asyncio.create_subprocess_exec(
                require("dsh"), "web", "--host", "127.0.0.1", "--port", str(port),
                "--no-open", "--public-url", origin + "/dsh/", "--trusted-host", urlsplit(origin).netloc,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=HOME, env=ai_env(), start_new_session=True,
            )
            reader = asyncio.create_task(stdout())
        for _ in range(100):
            if launch_token:
                if not native_cookie:
                    base = f"http://127.0.0.1:{port}"
                    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                        try:
                            response = await client.get(base + "/", params={"token": launch_token},
                                                        headers={"Origin": base})
                        except httpx.HTTPError:
                            raise HTTPException(502, "DSH Web 本机认证失败") from None
                    if response.status_code != 303 or not response.cookies:
                        raise HTTPException(502, "DSH Web 本机认证失败")
                    native_cookie = "; ".join(f"{key}={value}" for key, value in response.cookies.items())
                return {"url": "/dsh/"}
            if process.returncode is not None or reader.done():
                await stop()
                raise HTTPException(502, "DSH Web 启动失败。请在原生终端中检查 dsh web。")
            await asyncio.sleep(0.1)
        raise HTTPException(503, "DSH Web 仍在启动，请稍后重试")


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
