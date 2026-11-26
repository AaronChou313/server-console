import asyncio
import json
import os
import platform
import re
import shutil
import socket
import sys
import time
from pathlib import Path

import psutil
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .runtime import HOME, binary, directory, require, run

router = APIRouter()
previous = None


def interfaces():
    counters = psutil.net_io_counters(pernic=True)
    status = psutil.net_if_stats()
    return [{
        "name": name, "up": bool(status.get(name) and status[name].isup),
        "addresses": [addr.address for addr in addresses if addr.family in (socket.AF_INET, socket.AF_INET6)],
        "rx": counters[name].bytes_recv if name in counters else 0,
        "tx": counters[name].bytes_sent if name in counters else 0,
    } for name, addresses in psutil.net_if_addrs().items()]


@router.get("/api/overview")
def overview():
    global previous
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(HOME))
    # APFS volumes share free space. Report usable space on the user's volume.
    network = psutil.net_io_counters()
    now = time.monotonic()
    rx, tx = (network.bytes_recv, network.bytes_sent) if network else (0, 0)
    down = up = 0
    if previous and now > previous[0]:
        down = max(0, (rx - previous[1]) / (now - previous[0]))
        up = max(0, (tx - previous[2]) / (now - previous[0]))
    previous = now, rx, tx
    addresses = [a for i in interfaces() if i["up"] and not i["name"].startswith("lo")
                 for a in i["addresses"] if ":" not in a and not a.startswith("169.254.")]
    os_name = "macOS " + platform.mac_ver()[0] if sys.platform == "darwin" else platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    return {
        "hostname": socket.gethostname(), "os": os_name, "arch": platform.machine(),
        "ip": next(iter(addresses), ""), "addresses": addresses,
        "uptime": max(0, int(time.time() - psutil.boot_time())), "cpu": psutil.cpu_percent(),
        "cores": psutil.cpu_count(), "memory": {"used": memory.total - memory.available, "total": memory.total, "percent": memory.percent},
        "disk": {"used": disk.total - disk.free, "total": disk.total,
                 "percent": round(100 * (disk.total - disk.free) / disk.total, 1)},
        "net": {"rx": rx, "tx": tx, "down": down, "up": up},
    }


def connection_rows():
    rows = []
    for conn in psutil.net_connections(kind="inet"):
        if not conn.laddr:
            continue
        def endpoint(address):
            if not address:
                return ""
            host, port = address
            return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        rows.append({"protocol": "TCP" if conn.type == socket.SOCK_STREAM else "UDP",
                     "local": endpoint(conn.laddr), "remote": endpoint(conn.raddr),
                     "state": conn.status if conn.status != "NONE" else "", "pid": conn.pid})
    return rows


async def tailnet():
    tailscale = binary("tailscale")
    if not tailscale:
        return {"available": False, "state": "未安装", "ips": [], "peers": []}
    code, out, err = await run(tailscale, "status", "--json")
    try:
        response = json.loads(out)
    except ValueError:
        return {"available": True, "state": (err or out).strip() or "无法连接 Tailscale", "ips": [], "peers": []}
    own = response.get("Self") or {}
    return {
        "available": True, "state": response.get("BackendState") or "未知", "ips": response.get("TailscaleIPs") or [],
        "dns": (own.get("DNSName") or "").rstrip("."), "health": response.get("Health") or [],
        "peers": [{
            "name": peer.get("HostName"), "dns": (peer.get("DNSName") or "").rstrip("."),
            "ips": peer.get("TailscaleIPs") or [], "online": peer.get("Online", False),
            "active": peer.get("Active", False), "relay": peer.get("Relay"), "endpoint": peer.get("CurAddr"),
            "rx": peer.get("RxBytes", 0), "tx": peer.get("TxBytes", 0),
        } for peer in (response.get("Peer") or {}).values()],
    }


async def connections():
    try:
        return (await asyncio.to_thread(connection_rows))[:300], ""
    except psutil.AccessDenied:
        lsof = shutil.which("lsof")
        if not lsof:
            return [], "当前用户无权读取系统连接"
        _, out, _ = await run(lsof, "-nP", "-i", "-a", "-u", str(os.getuid()), "-FpPnT")
        rows, pid, proto = [], None, ""
        for line in out.splitlines():
            value = line[1:]
            if line.startswith("p"):
                pid = value
            elif line.startswith("P"):
                proto = value
            elif line.startswith("n"):
                local, _, remote = value.partition("->")
                rows.append({"protocol": proto, "local": local, "remote": remote, "state": "", "pid": pid})
            elif line.startswith("TST=") and rows:
                rows[-1]["state"] = line[4:]
        return rows[:300], "显示当前用户可读取的连接"


@router.get("/api/network")
async def network():
    (rows, note), tailscale = await asyncio.gather(connections(), tailnet())
    return {"interfaces": interfaces(), "connections": rows, "note": note, "tailscale": tailscale}


class Probe(BaseModel):
    target: str = Field(default="", max_length=253)
    mode: str = "ping"
    port: int = Field(default=443, ge=1, le=65535)


@router.post("/api/network/probe")
async def probe(form: Probe):
    target = form.target.strip()
    if form.mode == "netcheck":
        code, out, err = await run(require("tailscale"), "netcheck", timeout=20)
    elif not re.fullmatch(r"[A-Za-z0-9:_][A-Za-z0-9:.%_\-]*", target):
        raise HTTPException(400, "请输入主机名或 IP 地址")
    elif form.mode == "tailping":
        code, out, err = await run(require("tailscale"), "ping", "--c=2", "--timeout=3s", target, timeout=9)
    elif form.mode == "tcp":
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(target, form.port), 5)
            writer.close()
            await writer.wait_closed()
            code, out, err = 0, f"{target}:{form.port} · {(time.monotonic() - start) * 1000:.0f} ms", ""
        except (OSError, asyncio.TimeoutError) as error:
            code, out, err = 1, "", str(error) or "连接超时"
    elif form.mode == "ping":
        ping = require("ping6" if sys.platform == "darwin" and ":" in target else "ping")
        code, out, err = await run(ping, "-n", "-c", "2", target, timeout=7)
    else:
        raise HTTPException(400, "不支持该检测方式")
    return {"ok": code == 0, "output": (out + err).strip()}


@router.get("/api/directories")
def directories(path: str = Query(default="~", max_length=4096)):
    folder = directory(path)
    entries = []
    try:
        with os.scandir(folder) as children:
            for child in children:
                try:
                    if child.is_dir():
                        entries.append({"name": child.name, "path": child.path})
                except OSError:
                    continue
    except OSError as error:
        raise HTTPException(400, "无法读取此目录，请返回上一级或选择其他目录") from error
    return {
        "path": str(folder),
        "parent": str(folder.parent) if folder != folder.parent else None,
        "directories": sorted(entries, key=lambda child: (child["name"].casefold(), child["name"])),
    }


@router.get("/api/projects")
def projects():
    roots = [Path.cwd(), HOME / "Projects", HOME / "projects", HOME / "Developer", HOME / "code"]
    items = {HOME, Path.cwd()}
    for root in roots[1:]:
        try:
            items.update(path for path in root.iterdir() if not path.name.startswith(".") and path.is_dir())
        except OSError:
            continue
    return sorted(str(path) for path in items)
