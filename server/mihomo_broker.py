"""Client for the independent, same-host Mihomo service."""
import os
import sys
from pathlib import Path

import httpx
from fastapi import HTTPException

from .runtime import HOME


def socket_path():
    override = os.getenv("SERVER_CONSOLE_MIHOMO_BROKER")
    if override:
        return None if override == "off" else Path(override).expanduser()
    if sys.platform == "darwin":
        path = Path("/var/run/server-console-mihomo.sock")
        installed = Path("/Library/LaunchDaemons/local.server-console.mihomo.plist")
    else:
        path = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "server-console-mihomo.sock"
        installed = HOME / ".config/systemd/user/server-console-mihomo.service"
    return path if path.is_socket() or installed.is_file() else None


def migration_pending():
    return (sys.platform == "darwin" and not os.getenv("SERVER_CONSOLE_MIHOMO_BROKER")
            and Path("/var/run/openremotehub-proxy.sock").is_socket())


async def request(method, path, **kwargs):
    socket = socket_path()
    if not socket:
        raise HTTPException(503, "Mihomo 独立服务尚未安装")
    try:
        async with httpx.AsyncClient(
            base_url="http://localhost", transport=httpx.AsyncHTTPTransport(uds=str(socket)),
            trust_env=False, timeout=5,
        ) as upstream:
            response = await upstream.request(method, path, **kwargs)
        payload = response.json() if response.content else None
        if not response.is_success:
            raise HTTPException(response.status_code, (payload or {}).get("detail", "Mihomo 代理操作失败"))
        return payload
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(502, "无法连接独立 Mihomo 服务") from error


async def state():
    return await request("GET", "/engine/state")


async def change(action, source=None):
    current = await state()
    body = {"action": action, "revision": current["revision"]}
    if source is not None:
        body["source"] = source
    return await request("POST", "/engine/control", json=body, timeout=80)
