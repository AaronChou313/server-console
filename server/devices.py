"""Discover Server Console instances without sharing authentication across hosts."""
import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
import uuid
from contextlib import suppress
from urllib.parse import urlsplit

import httpx
import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import host
from .runtime import STATE, private_dir, read_json, replace

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
IDENTITY = "/api/identity"
SERVICE = "_server-console._tcp.local."
DEFAULT_PORT = 9786
TAILNETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
RANK = {"lan": 0, "tailscale": 1, "manual": 2}
MAX_DISCOVERED = 80
MAX_MANUAL = 32


def instance_id():
    path = STATE / "instance-id.json"
    if found := read_json(path, None):
        return found
    value = str(uuid.uuid4())
    replace(private_dir(STATE) / path.name, json.dumps(value))
    return value


@router.get("/identity")
async def identity():
    return {"service": "server-console", "id": instance_id()}


def saved():
    return read_json(STATE / "devices.json", {"manual": [], "known": []})


def save(values):
    replace(private_dir(STATE) / "devices.json", json.dumps(values, ensure_ascii=False, indent=2) + "\n")


def address_ip(value):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def origin_for(address, port):
    literal = f"[{address}]" if ":" in address else address
    return f"http://{literal}" if port == 80 else f"http://{literal}:{port}"


def normalize_origin(value):
    value = value.strip()
    try:
        if "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError()
        parts = urlsplit(value)
        url = httpx.URL(value)
        if (url.scheme in {"http", "https"} and url.host and not url.userinfo
                and parts.netloc and parts.username is None and "%" not in url.host
                and url.path == "/" and not url.query and (url.port is None or 1 <= url.port <= 65535)):
            return str(url.copy_with(query=None, fragment=None)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        pass
    raise HTTPException(422, "请输入 HTTP(S) 控制台根地址，例如 http://192.168.1.10:9786")


def lan_networks():
    up = psutil.net_if_stats()
    rows = []
    for name, addresses in psutil.net_if_addrs().items():
        if name.startswith(("lo", "utun", "tun", "tailscale", "wg")) or not (up.get(name) and up[name].isup):
            continue
        for entry in addresses:
            if entry.family not in (socket.AF_INET, socket.AF_INET6) or not entry.netmask:
                continue
            ip = address_ip(entry.address.split("%", 1)[0])
            mask = address_ip(entry.netmask.split("%", 1)[0])
            if (ip and mask and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)
                    and not any(ip in network for network in TAILNETS)):
                rows.append((name, ip, ipaddress.ip_network((ip, int(mask).bit_count()), strict=False)))
    return rows


class Discovery:
    def __init__(self):
        self.task = None
        self.mdns = None
        self.browser = None
        self.names = set()
        self.observed = {}
        self.scanning = asyncio.Lock()
        self.updated = 0
        self.tailscale = False

    def start(self):
        self.task = asyncio.create_task(self.start_lan())

    async def start_lan(self):
        try:
            from zeroconf import IPVersion, ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
        except ImportError:
            return
        try:
            networks = await asyncio.to_thread(lan_networks)
            if not networks:
                return
            ipv4 = {str(ip) for _, ip, _ in networks if ip.version == 4}
            ipv6 = {socket.if_nametoindex(name) for name, ip, _ in networks if ip.version == 6}
            interfaces = sorted(ipv4) + sorted(ipv6)
            ip_version = IPVersion.All if ipv4 and ipv6 else IPVersion.V4Only if ipv4 else IPVersion.V6Only
            self.mdns = AsyncZeroconf(interfaces=interfaces, ip_version=ip_version)

            def changed(zeroconf, service_type, name, state_change):
                if state_change is ServiceStateChange.Removed:
                    self.names.discard(name)
                elif len(self.names) < MAX_DISCOVERED:
                    self.names.add(name)

            self.browser = AsyncServiceBrowser(self.mdns.zeroconf, SERVICE, handlers=[changed])
            # Uvicorn opens its socket after the ASGI lifespan has started.
            for _ in range(20):
                listeners = await asyncio.to_thread(psutil.Process().net_connections, kind="inet")
                bound = [item.laddr for item in listeners if item.type == socket.SOCK_STREAM
                         and item.status == psutil.CONN_LISTEN]
                if bound:
                    break
                await asyncio.sleep(0.15)
            if not bound:
                return
            configured = os.getenv("SERVER_CONSOLE_PORT", str(DEFAULT_PORT))
            bind_ip, port = next((item for item in bound if str(item.port) == configured), bound[0])
            actual = address_ip(bind_ip.split("%", 1)[0])
            addresses = [str(ip) for _, ip, _ in networks if actual and
                         (ip == actual or (actual.is_unspecified and actual.version == ip.version))]
            if addresses:
                node_id = instance_id()
                label = "sc-" + node_id.replace("-", "")
                info = AsyncServiceInfo(SERVICE, f"{label}.{SERVICE}", port=port, parsed_addresses=addresses,
                                        server=f"{label}.local.", properties={"name": socket.gethostname()[:100]})
                await self.mdns.async_register_service(info)
        except Exception as error:
            logger.debug("DNS-SD unavailable: %s", error)

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.browser:
            with suppress(Exception):
                await self.browser.async_cancel()
        if self.mdns:
            with suppress(Exception):
                await self.mdns.async_close()

    async def lan(self):
        if self.mdns is None:
            return []
        networks = [net for _, _, net in await asyncio.to_thread(lan_networks)]
        # Let a newly started browser receive DNS-SD replies before resolving.
        await asyncio.sleep(0.35)
        infos = await asyncio.gather(*(self.mdns.async_get_service_info(SERVICE, name, timeout=850)
                                       for name in sorted(self.names)), return_exceptions=True)
        rows = []
        for info in infos:
            if not info or isinstance(info, Exception) or not info.port:
                continue
            name = (info.properties.get(b"name") or b"").decode(errors="replace")[:100]
            for address in info.parsed_addresses():
                ip = address_ip(address)
                if ip and any(ip in network for network in networks):
                    rows.append({"source": "lan", "url": origin_for(address, info.port), "name": name})
        return rows

    async def tailnet(self):
        data = await asyncio.wait_for(host.tailnet(), 3)
        self.tailscale = data["available"] and data["state"] == "Running"
        if not self.tailscale:
            return []
        return [{"source": "tailscale", "url": origin_for(address, DEFAULT_PORT), "name": (peer["name"] or "")[:100]}
                for peer in data["peers"] if peer["online"] for address in peer["ips"]
                if (ip := address_ip(address)) and any(ip in network for network in TAILNETS)]

    def snapshot(self):
        values = saved()
        current_id = instance_id()
        groups = {}
        current_manual = []
        for row in [*values["manual"], *values["known"]]:
            device_id = row.get("instance")
            if device_id == current_id:
                if "key" in row:
                    current_manual.append(row)
                continue
            key = device_id or row["key"]
            device = groups.setdefault(key, {"id": key, "name": "", "addresses": {}, "manual": []})
            device["name"] = device["name"] or row["name"]
            address = device["addresses"].setdefault(row["url"], {
                "url": row["url"], "sources": [], "reachable": self.observed.get(row["url"]),
            })
            source = row.get("source", "manual")
            if source not in address["sources"]:
                address["sources"].append(source)
            if source == "manual":
                device["manual"].append({key: row[key] for key in ("key", "name", "url")})
        for device in groups.values():
            device["addresses"] = sorted(device["addresses"].values(), key=lambda row: (
                {True: 0, None: 1, False: 2}[row["reachable"]], min(RANK[source] for source in row["sources"]),
            ))
            device["name"] = device["name"] or urlsplit(device["addresses"][0]["url"]).hostname
        return {"devices": sorted(groups.values(), key=lambda row: (row["name"].casefold(), row["id"])),
                "current_manual": current_manual,
                "lan": self.browser is not None, "tailscale": self.tailscale}

    async def refresh(self):
        async with self.scanning:
            if time.monotonic() - self.updated < 3:
                return self.snapshot()
            results = await asyncio.gather(self.lan(), self.tailnet(), return_exceptions=True)
            if isinstance(results[1], Exception):
                self.tailscale = False
            automatic = {(row["source"], row["url"]): row for result in results if isinstance(result, list)
                         for row in result}
            automatic = dict(list(automatic.items())[:MAX_DISCOVERED])
            initial = saved()
            candidates = dict.fromkeys(row["url"] for row in [*automatic.values(), *initial["known"]])
            urls = set(list(candidates)[:MAX_DISCOVERED]) | {row["url"] for row in initial["manual"]}
            limit = asyncio.Semaphore(20)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=1.1) as client:
                async def check(url):
                    async with limit:
                        return url, await probe(client, url)

                found = dict(await asyncio.gather(*(check(url) for url in urls)))
            self.observed = {url: bool(device_id) for url, device_id in found.items()}
            values = saved()
            known = {(row["source"], row["url"]): row for row in values["known"]}
            current_id = instance_id()
            for row in values["manual"]:
                if device_id := found.get(row["url"]):
                    row["instance"] = device_id
            for key, row in {**known, **automatic}.items():
                if device_id := found.get(row["url"]):
                    known.pop(key, None)
                    if device_id != current_id:
                        known[key] = {**row, "instance": device_id}
            values["known"] = list(known.values())[-MAX_DISCOVERED:]
            if values != initial:
                save(values)
            self.updated = time.monotonic()
            return self.snapshot()


async def probe(client, url):
    try:
        async with asyncio.timeout(1.3), client.stream("GET", url + IDENTITY) as response:
            if response.status_code != 200 or "application/json" not in response.headers.get("content-type", "").lower():
                return None
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=2048):
                content.extend(chunk)
                if len(content) > 1024:
                    return None
            data = json.loads(content)
            if isinstance(data, dict) and data.get("service") == "server-console" and isinstance(data.get("id"), str):
                return str(uuid.UUID(data["id"]))
    except (httpx.HTTPError, TimeoutError, ValueError):
        return None
    return None


discovery = Discovery()


class ManualDevice(BaseModel):
    url: str = Field(max_length=512)
    name: str = Field(default="", max_length=100)


@router.get("/devices")
async def devices():
    return discovery.snapshot()


@router.post("/devices/refresh")
async def refresh():
    return await discovery.refresh()


async def update_device(form, key=None):
    url = normalize_origin(form.url)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=1.1) as client:
        device_id = await probe(client, url)
    values = saved()
    manual = values["manual"]
    row = next((row for row in manual if row["key"] == key), None)
    if key is not None and row is None:
        raise HTTPException(404, "手动地址不存在")
    if any(item["url"] == url and item["key"] != key for item in manual):
        raise HTTPException(409, "这个地址已添加")
    if row is None:
        if len(manual) >= MAX_MANUAL:
            raise HTTPException(409, "手动地址已达上限")
        row = {"key": uuid.uuid4().hex}
        manual.append(row)
    if row.get("url") != url:
        row.pop("instance", None)
    row.update(url=url, name=form.name.strip())
    discovery.observed[url] = bool(device_id)
    if device_id:
        for known in values["known"]:
            if known["url"] == url:
                known["instance"] = device_id
    device_id = device_id or row.get("instance") or next(
        (known["instance"] for known in values["known"] if known["url"] == url), None,
    )
    if device_id:
        row["instance"] = device_id
    save(values)
    discovery.updated = 0
    return discovery.snapshot()


@router.post("/devices")
async def add(form: ManualDevice):
    return await update_device(form)


@router.put("/devices/{key}")
async def edit(key: str, form: ManualDevice):
    return await update_device(form, key)


@router.delete("/devices/{key}")
async def delete(key: str):
    values = saved()
    rows = [row for row in values["manual"] if row["key"] != key]
    if len(rows) == len(values["manual"]):
        raise HTTPException(404, "手动地址不存在")
    values["manual"] = rows
    save(values)
    return discovery.snapshot()
