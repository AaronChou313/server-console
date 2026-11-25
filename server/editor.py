"""Run code-server on a private Unix socket behind the console's login."""
import asyncio
import logging
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.responses import RedirectResponse, Response, StreamingResponse
from websockets.asyncio.client import unix_connect
from websockets.exceptions import WebSocketException

from .dsh import HOP, browser_origin, terminate
from .runtime import ENV, HOME, directory, require

router = APIRouter()
PREFIX = "/editor"
log = logging.getLogger(__name__)
lock = asyncio.Lock()
process = None
runtime = None
sockets = {}


class Workspace(BaseModel):
    folder: str = Field(min_length=1, max_length=4096)


def executable_socket():
    return str(Path(runtime.name) / "code.sock") if process and process.returncode is None else None


async def stop():
    global process, runtime
    await terminate(process, None)
    if runtime:
        runtime.cleanup()
    process = runtime = None


def client_for(socket, timeout):
    return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), timeout=timeout, trust_env=False)


async def start():
    global process, runtime
    async with lock:
        if executable_socket():
            return
        await stop()
        binary = require("code-server")
        # A short, per-process directory stays within macOS's Unix socket path limit.
        runtime = tempfile.TemporaryDirectory(prefix="sc-editor-", dir="/tmp")
        folder = Path(runtime.name)
        config = folder / "config.yaml"
        config.write_text("{}\n")
        try:
            process = await asyncio.create_subprocess_exec(
                binary, "--config", str(config), "--socket", str(folder / "code.sock"),
                "--socket-mode", "0600", "--auth", "none", "--disable-proxy",
                "--session-socket", str(folder / "session.sock"),
                cwd=HOME, env=ENV, start_new_session=True,
            )
            async with asyncio.timeout(15), client_for(str(folder / "code.sock"), 1) as client:
                while process.returncode is None:
                    try:
                        response = await client.get("http://localhost/healthz")
                        if response.is_success:
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.15)
            raise HTTPException(502, "code-server 提前退出，请检查服务日志")
        except (OSError, TimeoutError) as error:
            await stop()
            log.warning("code-server failed to start: %s", error)
            raise HTTPException(502, "无法启动 code-server，请检查服务日志") from error
        except (asyncio.CancelledError, HTTPException):
            await stop()
            raise


@router.post("/api/editor/open")
async def open_workspace(workspace: Workspace):
    folder = directory(workspace.folder.strip())
    await start()
    return {"url": PREFIX + "/?" + urlencode({"folder": str(folder)})}


@router.get(PREFIX)
async def editor_root():
    return RedirectResponse(PREFIX + "/")


def upstream_path(connection):
    path = connection.scope["raw_path"].removeprefix(PREFIX.encode())
    query = connection.scope["query_string"]
    return path + (b"?" + query if query else b"")


@router.api_route(PREFIX + "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(request: Request, path: str):
    if not path and request.method == "GET":
        await start()
    socket = executable_socket()
    if not socket:
        raise HTTPException(503, "请先从控制台打开代码编辑器")
    client = client_for(socket, httpx.Timeout(10, read=None))
    url = httpx.URL("http://localhost").copy_with(raw_path=upstream_path(request))
    excluded = HOP | {"cookie", "authorization", "x-server-console"}
    headers = [(key, value) for key, value in request.headers.items()
               if key not in excluded and not key.startswith("x-forwarded-")]
    headers.append(("x-forwarded-proto", request.url.scheme))
    content = request.stream() if request.method not in ("GET", "HEAD") else None
    try:
        upstream = await client.send(client.build_request(
            request.method, url, headers=headers, content=content,
        ), stream=True)
    except (ClientDisconnect, httpx.HTTPError, asyncio.CancelledError) as error:
        await client.aclose()
        if isinstance(error, ClientDisconnect):
            return Response(status_code=499)
        if isinstance(error, asyncio.CancelledError):
            raise
        raise HTTPException(502, "无法连接代码编辑器") from error

    async def release():
        await upstream.aclose()
        await client.aclose()

    response = StreamingResponse(upstream.aiter_raw(), upstream.status_code, background=BackgroundTask(release))
    for key, value in upstream.headers.multi_items():
        if key in HOP or key in {"set-cookie", "cache-control"}:
            continue
        if key == "location":
            location = urlsplit(value)
            if location.path.startswith("/") and location.netloc in {"", "localhost", request.headers["host"]}:
                target = location.path if location.path.startswith(PREFIX + "/") else PREFIX + location.path
                value = location._replace(scheme="", netloc="", path=target).geturl()
        response.raw_headers.append((key.encode("ascii"), value.encode("latin-1")))
    response.headers["cache-control"] = "private, no-store"
    return response


def disconnect(token):
    for closed in sockets.get(token, ()):
        closed.set()


@router.websocket(PREFIX + "/{path:path}")
async def websocket(ws: WebSocket, path: str):
    socket = executable_socket()
    if not socket:
        await ws.close(code=1013)
        return
    token, expires = ws.scope["editor_session"]
    closed = asyncio.Event()
    sockets.setdefault(token, set()).add(closed)
    try:
        async with asyncio.timeout(max(0, expires - time.monotonic())), unix_connect(
            socket, uri="ws://" + ws.headers["host"] + upstream_path(ws).decode("ascii"),
            origin=browser_origin(ws), additional_headers={"X-Forwarded-Proto": browser_origin(ws).split(":", 1)[0]},
            subprotocols=ws.scope["subprotocols"] or None, max_size=16 * 1024 * 1024,
            open_timeout=5, close_timeout=2,
        ) as upstream:
            await ws.accept(subprotocol=upstream.subprotocol)

            async def from_browser():
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await upstream.send(message["text"] if "text" in message else message["bytes"])

            async def from_editor():
                async for message in upstream:
                    if isinstance(message, str):
                        await ws.send_text(message)
                    else:
                        await ws.send_bytes(message)

            tasks = [asyncio.create_task(task()) for task in (from_browser, from_editor, closed.wait)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if closed.is_set():
                    await ws.close(code=1008)
                    return
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, TimeoutError, WebSocketException, WebSocketDisconnect) as error:
        log.debug("code-server WebSocket: %s", error)
    finally:
        registered = sockets[token]
        registered.discard(closed)
        if not registered:
            del sockets[token]
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.close(code=1008 if closed.is_set() or expires <= time.monotonic() else 1000)
