import asyncio
import html
import re
import socket
import time
from contextlib import suppress

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import dsh
from .runtime import binary, run

router = APIRouter()
CACHE_TTL = 12
TUNNEL_IDLE = 600
SWEEP = 30
LOOPBACK = {"127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "::ffff:127.0.0.1"}
WILDCARD = {"0.0.0.0", "::", "*", ""}
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
cache = {"key": None, "at": 0.0, "items": []}
tunnels = {}
scan_lock = asyncio.Lock()


def loopback(address):
    return address in LOOPBACK or address == "localhost" or address.startswith("127.")


def host(address):
    return f"[{address}]" if ":" in address else address


def name_of(pid):
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, TypeError, ValueError):
        return ""


def parse_lsof(output):
    rows, pid, command = [], None, ""
    for line in output.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            pid = int(value) if value.isdigit() else None
        elif tag == "c":
            command = value
        elif tag == "n" and pid:
            address, _, port = value.rpartition(":")
            if not port.isdigit():
                continue
            address = address.split("%", 1)[0]
            if address.startswith("[") and address.endswith("]"):
                address = address[1:-1]
            rows.append((address or "*", int(port), pid, command))
    return rows


async def listeners():
    """Return (address, port, pid, process) for every listening TCP socket."""
    rows = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.type == socket.SOCK_STREAM and conn.status == psutil.CONN_LISTEN and conn.laddr:
                rows.append((conn.laddr.ip, conn.laddr.port, conn.pid, name_of(conn.pid) if conn.pid else ""))
        return rows
    except (psutil.Error, OSError):
        pass
    # macOS restricts net_connections to the current user's processes.
    lsof = binary("lsof")
    if not lsof:
        return []
    _, out, _ = await run(lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn", "+c", "0", timeout=8)
    return parse_lsof(out)


def aggregate(rows):
    items = {}
    for address, port, pid, name in rows:
        if not 0 < port < 65536:
            continue
        item = items.setdefault(port, {"port": port, "addresses": set(), "pid": None, "process": ""})
        item["addresses"].add(address)
        if pid and item["pid"] is None:
            item["pid"] = pid
        if name and not item["process"]:
            item["process"] = name
    return items


def probe_address(item):
    addresses = item["addresses"]
    if "127.0.0.1" in addresses or addresses & {"0.0.0.0", "*", ""}:
        return "127.0.0.1"
    if "::" in addresses:
        return "::1"
    return sorted(addresses)[0]


async def probe(client, item):
    target = f"{host(probe_address(item))}:{item['port']}"
    try:
        async with client.stream("GET", f"http://{target}/",
                                 headers={"Host": target, "Accept": "text/html,application/xhtml+xml"}) as response:
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= 65536:
                    break
            status = response.status_code
            kind = (response.headers.get("content-type") or "").lower()
            server = (response.headers.get("server") or "")[:80]
    except (httpx.HTTPError, OSError, asyncio.TimeoutError):
        return None
    text = b"".join(chunks).decode("utf-8", "replace")
    markup = "html" in kind or bool(re.match(r"\s*<(!doctype|html)", text[:64], re.I))
    # Keep HTML pages and redirects to login pages; drop APIs, databases and protocol noise.
    if status >= 400 or (not markup and status < 300):
        return None
    match = TITLE.search(text) if markup else None
    # A 200 HTML page without a title is usually a machine endpoint (Tailscale peer API, proxies).
    if status < 300 and markup and not match:
        return None
    title = ""
    if match:
        title = " ".join(html.unescape(match.group(1)).split())[:80]
        if title.lower().startswith("directory listing"):
            title = ""
    return {**item, "title": title, "server": server}


async def scan(exclude):
    items = [item for port, item in aggregate(await listeners()).items() if port not in exclude]
    if not items:
        return []
    limit = asyncio.Semaphore(16)

    async def guarded(item):
        async with limit:
            try:
                return await probe(client, item)
            except (httpx.InvalidURL, UnicodeError, ValueError):
                return None

    async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(1.2, connect=0.7)) as client:
        found = await asyncio.gather(*(guarded(item) for item in items))
    return [item for item in found if item]


def direct_url(item, visible):
    port = item["port"]
    if visible in item["addresses"] and not loopback(visible):
        return f"http://{host(visible)}:{port}/"
    if not loopback(visible) and item["addresses"] & WILDCARD:
        return f"http://{host(visible)}:{port}/"
    for address in sorted(item["addresses"]):
        if not loopback(address) and address not in WILDCARD:
            return f"http://{host(address)}:{port}/"
    return None


def local(item):
    address = next((value for value in sorted(item["addresses"]) if value not in WILDCARD), "0.0.0.0")
    return f"{host(address)}:{item['port']}"


async def detect(exclude, refresh=False):
    key = tuple(sorted(exclude))
    now = time.monotonic()
    if not refresh and cache["key"] == key and now - cache["at"] < CACHE_TTL:
        return cache["items"]
    async with scan_lock:
        now = time.monotonic()
        if not refresh and cache["key"] == key and now - cache["at"] < CACHE_TTL:
            return cache["items"]
        items = await scan(set(exclude))
        items.sort(key=lambda item: (direct_url(item, "") is None, (item["title"] or item["process"]).casefold()))
        cache.update(key=key, at=time.monotonic(), items=items)
        return items


def excluded(request):
    ports = set()
    server = request.scope.get("server") or ()
    if len(server) > 1 and server[1]:
        ports.add(int(server[1]))
    if dsh.port:
        ports.add(int(dsh.port))
    ports.update(tunnel["port"] for tunnel in tunnels.values())
    return frozenset(ports)


@router.get("/api/apps")
async def apps(request: Request, refresh: bool = False):
    items = await detect(excluded(request), refresh=refresh)
    visible = request.url.hostname or "127.0.0.1"
    bind = (request.scope.get("server") or ("127.0.0.1", 0))[0]
    result = []
    for item in items:
        port = item["port"]
        url = direct_url(item, visible)
        result.append({
            "name": item["title"] or item["process"] or "Web 服务", "port": port,
            "local": local(item), "upstream": f"{host(probe_address(item))}:{port}",
            "url": url or f"/apps/{port}/", "proxied": url is None,
            "local_only": url is None and (loopback(bind) or bind in WILDCARD),
        })
    return {"apps": result}


async def copy(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        with suppress(Exception):
            writer.close()


async def shutdown(port):
    tunnel = tunnels.pop(port, None)
    if not tunnel:
        return
    tunnel["closed"] = True
    tunnel["server"].close()
    with suppress(Exception):
        await tunnel["server"].wait_closed()


async def sweep(port):
    while True:
        await asyncio.sleep(SWEEP)
        tunnel = tunnels.get(port)
        if not tunnel or tunnel["closed"]:
            return
        if tunnel["active"] == 0 and time.monotonic() - tunnel["last"] > TUNNEL_IDLE:
            await shutdown(port)
            return


async def forward(port, bind, upstream):
    existing = tunnels.get(port)
    if existing and existing["bind"] == bind:
        existing["last"] = time.monotonic()
        return existing
    if existing:
        await shutdown(port)
    tunnel = {"bind": bind, "last": time.monotonic(), "active": 0, "server": None, "port": 0, "closed": False}

    async def handle(reader, writer):
        tunnel["active"] += 1
        tunnel["last"] = time.monotonic()
        try:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(upstream, port), 3)
            except (OSError, asyncio.TimeoutError):
                return
            await asyncio.gather(copy(reader, upstream_writer), copy(upstream_reader, writer))
        finally:
            tunnel["active"] -= 1
            tunnel["last"] = time.monotonic()
            with suppress(Exception):
                writer.close()

    tunnel["server"] = await asyncio.start_server(handle, bind, 0, backlog=64)
    tunnel["port"] = tunnel["server"].sockets[0].getsockname()[1]
    tunnels[port] = tunnel
    asyncio.create_task(sweep(port))
    return tunnel


@router.get("/apps/{port}")
@router.get("/apps/{port}/")
async def open_app(port: int, request: Request):
    items = await detect(excluded(request))
    item = next((value for value in items if value["port"] == port), None)
    if item is None:
        raise HTTPException(404, "没有找到这个 Web 服务")
    visible = request.url.hostname or "127.0.0.1"
    if direct_url(item, visible):
        raise HTTPException(409, "该服务可以直接访问")
    server = request.scope.get("server") or ("", 0)
    bind = server[0]
    if not bind or bind in WILDCARD or (loopback(bind) and not loopback(visible)):
        raise HTTPException(409, "请通过本机浏览器访问，或为此应用单独建立 SSH 端口转发")
    tunnel = await forward(port, bind, probe_address(item))
    return RedirectResponse(f"http://{host(bind)}:{tunnel['port']}/",
                            status_code=302, headers={"Cache-Control": "no-store"})


async def stop():
    for port in list(tunnels):
        await shutdown(port)
