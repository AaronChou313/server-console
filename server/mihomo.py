import asyncio
import base64
import binascii
import gzip
import hashlib
import json
import json
import os
import platform
import re
import socket
import time
import uuid
from typing import Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from . import mihomo_broker as broker
from .runtime import ENV, STATE, binary, private_dir, read_json, replace, run

router = APIRouter(prefix="/api/mihomo")
HOME = STATE / "mihomo"
CONFIG = HOME / "config.json"
CONTROL = HOME / "control.sock"
ENABLED = HOME / "enabled"
LIBRARY = HOME / "profiles.json"
MANAGED_ENGINE = HOME / "bin/mihomo"
LIMIT = 2_000_000
PROXY_PORT = 17890
PROXY = f"http://127.0.0.1:{PROXY_PORT}"
LOCK = asyncio.Lock()
DELAY_SLOTS = asyncio.Semaphore(4)
SELECTABLE = {"Selector", "URLTest", "Fallback"}
MODES = Literal["rule", "global", "direct"]
process = None

PROFILE_KEYS = (
    "proxies", "proxy-groups", "proxy-providers", "rules", "rule-providers", "mode",
    "ipv6", "unified-delay", "tcp-concurrent", "global-client-fingerprint",
    "log-level", "geodata-mode", "geodata-loader", "geox-url", "dns", "hosts",
)
CHECKS = {
    "openai": "https://api.openai.com/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
}


class Profile(BaseModel):
    name: str = Field(default="", max_length=120)
    text: str | None = Field(default=None, max_length=LIMIT)
    url: str | None = Field(default=None, max_length=4096)


class Mode(BaseModel):
    mode: MODES


class Delay(BaseModel):
    name: str = Field(min_length=1, max_length=1024)


class Choice(BaseModel):
    group: str = Field(min_length=1, max_length=1024)
    node: str | None = Field(default=None, max_length=1024)


def normalize(source, identity="default"):
    if not isinstance(source, dict):
        raise HTTPException(422, "请输入标准 Clash / Mihomo YAML 配置")
    config = {key: source[key] for key in PROFILE_KEYS if key in source}
    for kind in ("proxy-providers", "rule-providers"):
        providers = config.get(kind, {})
        if not isinstance(providers, dict):
            raise HTTPException(422, "Provider 必须按名称配置")
        sanitized = {}
        for name, item in providers.items():
            if not isinstance(name, str) or not isinstance(item, dict) or item.get("type") not in ("http", "inline"):
                raise HTTPException(422, "仅支持 HTTP 或 inline Provider")
            if item["type"] == "http":
                url = urlsplit(str(item.get("url", "")))
                if url.scheme not in ("http", "https") or not url.hostname:
                    raise HTTPException(422, "Provider 需要有效的 HTTP(S) 地址")
            filename = hashlib.sha256(f"{identity}:{kind}:{name}".encode()).hexdigest()[:24]
            sanitized[name] = {**item, "path": f"providers/{filename}.yaml"}
        if sanitized:
            config[kind] = sanitized
    config.setdefault("rules", ["MATCH,DIRECT"])
    config.setdefault("log-level", "info")
    dns = config.get("dns", {})
    if not isinstance(dns, dict):
        raise HTTPException(422, "DNS 配置格式无效")
    config["dns"] = {key: value for key, value in dns.items() if key != "listen"}
    config.update({
        "mixed-port": PROXY_PORT, "allow-lan": False, "bind-address": "127.0.0.1",
        "external-controller-unix": str(CONTROL), "tun": {"enable": False},
        "profile": {"store-selected": True},
    })
    return json.dumps(config, ensure_ascii=False, indent=2)


def executable():
    return str(MANAGED_ENGINE) if os.access(MANAGED_ENGINE, os.X_OK) else binary("mihomo")


def require_engine():
    path = executable()
    if not path:
        raise HTTPException(503, "请先安装 Mihomo")
    return path


def controller():
    return httpx.AsyncClient(base_url="http://localhost", transport=httpx.AsyncHTTPTransport(uds=str(CONTROL)),
                             trust_env=False, timeout=3)


def running():
    return process is not None and process.returncode is None


async def stop():
    global process
    if running():
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 4)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    process = None
    CONTROL.unlink(missing_ok=True)


async def start():
    global process
    engine = require_engine()
    if running():
        return
    private_dir(HOME)
    if not CONFIG.exists():
        replace(CONFIG, normalize({}))
    CONTROL.unlink(missing_ok=True)
    with (HOME / "mihomo.log").open("ab") as log:
        process = await asyncio.create_subprocess_exec(
            engine, "-d", str(HOME), "-f", str(CONFIG),
            env=ENV, stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log,
        )
    try:
        async with controller() as client:
            for _ in range(35):
                if not running():
                    break
                if CONTROL.is_socket():
                    try:
                        if (await client.get("/version")).is_success:
                            return
                    except httpx.RequestError:
                        pass
                await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        await stop()
        raise
    await stop()
    raise HTTPException(502, "Mihomo 启动失败，请查看主机配置目录下的 mihomo/mihomo.log")


async def restore():
    if broker.socket_path() or broker.migration_pending():
        return
    if ENABLED.is_file() and executable():
        try:
            async with LOCK:
                await start()
        except (HTTPException, OSError):
            pass


async def control(method, path, **kwargs):
    if broker.socket_path():
        return await broker.request(method, path, **kwargs)
    if not running():
        raise HTTPException(409, "请先启动 Mihomo")
    try:
        async with controller() as client:
            response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, "Mihomo 控制接口请求失败") from error


async def snapshot():
    version, config, proxies = await asyncio.gather(
        control("GET", "/version"), control("GET", "/configs"), control("GET", "/proxies"),
    )
    nodes = proxies["proxies"]
    return {
        "version": version["version"], "mode": config.get("mode"),
        "groups": [{
            "name": name, "current": group.get("now"), "fixed": group.get("fixed"),
            "type": group["type"], "nodes": group["all"],
        } for name, group in nodes.items() if "all" in group
            and (name != "GLOBAL" or config.get("mode") == "global")],
        "nodes": {name: {"type": info.get("type"), "alive": info.get("alive"),
                         "history": (info.get("history") or [])[-1:]}
                  for name, info in nodes.items() if "all" not in info},
    }


@router.get("")
async def status():
    if broker.socket_path():
        shared = {"owner": "service", "available": True, "groups": [], "engine_ready": False}
        try:
            state = await broker.state()
            details = await snapshot() if state["ready"] else {}
            return {**shared, **details, "service_online": True, "engine_ready": state["ready"],
                    "state": "running" if state["ready"] and state["enabled"] else "error" if state["enabled"] else "stopped",
                    "port": state["mixed_port"], "pid": state["pid"], "configured": state["configured"],
                    "system_tun": state["tun"]}
        except HTTPException as error:
            return {**shared, "state": "error", "error": error.detail}
    if broker.migration_pending():
        return {"owner": "legacy", "available": False, "state": "migration", "groups": []}
    available = bool(executable())
    state = "running" if running() else "error" if ENABLED.exists() else "stopped"
    return {
        "available": available, "state": state, "port": PROXY_PORT,
        "configured": CONFIG.is_file(),
        **(await snapshot() if state == "running" else {"groups": []}),
    }


@router.post("/start")
async def enable():
    if broker.socket_path():
        async with LOCK:
            await broker.change("enable")
        return {"ok": True}
    if broker.migration_pending():
        raise HTTPException(409, "请先迁移 Mac 上已有的 Mihomo 服务")
    async with LOCK:
        await start()
        replace(ENABLED, "1\n")
    return {"ok": True}


@router.post("/stop")
async def disable():
    if broker.socket_path():
        async with LOCK:
            await broker.change("disable")
        return {"ok": True}
    async with LOCK:
        ENABLED.unlink(missing_ok=True)
        await stop()
    return {"ok": True}


def library():
    data = read_json(LIBRARY, None)
    if data is not None:
        return data
    current = read_json(CONFIG, {})
    if any(current.get(key) for key in ("proxies", "proxy-groups", "proxy-providers", "rule-providers")):
        identity = uuid.uuid4().hex
        data = {"active": identity, "items": {identity: {
            "name": "现有配置", "source": yaml.safe_dump({key: current[key] for key in PROFILE_KEYS if key in current},
                                                    allow_unicode=True), "url": None, "updated": time.time(),
        }}}
        save_library(data)
        return data
    return {"active": None, "items": {}}


def save_library(data):
    private_dir(HOME)
    replace(LIBRARY, json.dumps(data, ensure_ascii=False))


def find(data, identity):
    if identity not in data["items"]:
        raise HTTPException(404, "配置不存在")
    return data["items"][identity]


def source_url(url):
    if not url:
        return None
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return parsed.geturl()
    except ValueError:
        pass
    raise HTTPException(422, "请输入有效的 HTTP(S) 订阅地址")


def local_proxy():
    try:
        with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=0.3):
            return f"http://127.0.0.1:{PROXY_PORT}"
    except OSError:
        return None


async def fetch(url, proxy, ua="mihomo"):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8), follow_redirects=True,
                                     proxy=proxy, trust_env=False,
                                     headers={"User-Agent": ua}) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise HTTPException(502, f"订阅地址返回 HTTP {response.status_code}")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > LIMIT:
                        raise HTTPException(413, "订阅内容超过 2 MB")
                usage = {}
                for field in response.headers.get("subscription-userinfo", "").split(";"):
                    key, _, value = field.strip().partition("=")
                    if key in ("upload", "download", "total", "expire") and re.fullmatch(r"[0-9]{1,18}", value):
                        usage[key] = int(value)
                return bytes(content).decode("utf-8-sig"), usage
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(502, "订阅地址连接超时") from None
    except httpx.HTTPError as error:
        raise HTTPException(502, f"无法连接订阅地址（{type(error).__name__}）") from None
    except UnicodeError:
        raise HTTPException(502, "订阅内容不是 UTF-8 文本") from None


async def download(url):
    proxy = None
    try:
        text, usage = await fetch(url, None)
    except HTTPException as direct:
        # Only transport failures are worth retrying; an HTTP status is definitive.
        if direct.status_code == 413 or str(direct.detail).startswith("订阅地址返回 HTTP"):
            raise
        # A blocked network often still works through the local Mihomo mixed port.
        proxy = local_proxy()
        if not proxy:
            raise
        try:
            text, usage = await fetch(url, proxy)
        except HTTPException as proxied:
            raise HTTPException(502, f"直连失败（{direct.detail}），经本机代理仍失败（{proxied.detail}）") from None
    # Some providers only return Clash YAML for recognized clients; otherwise we convert Base64 below.
    if node_list(text):
        try:
            alternate, alternate_usage = await fetch(url, proxy, ua="clash-verge/v1.7.7")
        except HTTPException:
            alternate = ""
        if alternate and not node_list(alternate):
            return alternate, alternate_usage
    return text, usage


def _b64(value):
    raw = value.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")


def _query(parsed):
    return {key: values[-1] for key, values in parse_qs(parsed.query).items()}


def _label(parsed, fallback):
    return unquote(parsed.fragment).strip() or fallback


def _parse_ss(link):
    parsed = urlsplit(link)
    if parsed.hostname:
        host, port = parsed.hostname, parsed.port
        try:
            credential = _b64(parsed.username or "")
        except (ValueError, binascii.Error):
            credential = unquote(parsed.username or "")
        method, _, password = credential.partition(":")
        plugin = _query(parsed).get("plugin", "")
    else:
        # Legacy ss://BASE64(method:password@host:port)#name
        raw = _b64(parsed.netloc)
        credential, _, endpoint = raw.rpartition("@")
        method, _, password = credential.partition(":")
        host, _, port = endpoint.rpartition(":")
        port = int(port) if port.isdigit() else 0
        plugin = ""
    if not (host and port and method):
        return None
    proxy = {"name": _label(parsed, f"{host}:{port}"), "type": "ss", "server": host, "port": port,
             "cipher": method, "password": password, "udp": True}
    if plugin:
        parts = plugin.split(";")
        kind, options = parts[0].split("=")[0], dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        if kind in ("obfs-local", "simple-obfs", "obfs"):
            proxy["plugin"] = "obfs"
            proxy["plugin-opts"] = {"mode": options.get("obfs", "http")}
            if options.get("obfs-host"):
                proxy["plugin-opts"]["host"] = options["obfs-host"]
        elif kind == "v2ray-plugin":
            proxy["plugin"] = "v2ray-plugin"
            proxy["plugin-opts"] = {"mode": "websocket", "tls": options.get("tls") in ("1", "true")}
            if options.get("host"):
                proxy["plugin-opts"]["host"] = options["host"]
            if options.get("path"):
                proxy["plugin-opts"]["path"] = options["path"]
        else:
            return None
    return proxy


def _parse_vmess(link):
    data = json.loads(_b64(link[8:]))
    if not isinstance(data, dict):
        return None
    server, uuid = str(data.get("add") or "").strip(), str(data.get("id") or "").strip()
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError):
        return None
    if not (server and uuid and 0 < port < 65536):
        return None
    proxy = {"name": str(data.get("ps") or server).strip(), "type": "vmess", "server": server, "port": port,
             "uuid": uuid, "alterId": int(data.get("aid") or 0), "cipher": str(data.get("scy") or "auto"), "udp": True}
    host = str(data.get("host") or "").strip()
    sni = str(data.get("sni") or "").strip() or (host if host and not host.startswith("*") else "")
    if str(data.get("tls") or "").lower() in ("tls", "true", "1"):
        proxy["tls"] = True
        if sni:
            proxy["servername"] = sni
    network, path = str(data.get("net") or "tcp").lower(), str(data.get("path") or "").strip()
    if network == "ws":
        proxy["network"] = "ws"
        options = {}
        if path:
            options["path"] = path
        if host:
            options["headers"] = {"Host": host}
        if options:
            proxy["ws-opts"] = options
    elif network == "grpc":
        proxy["network"] = "grpc"
        if path:
            proxy["grpc-opts"] = {"grpc-service-name": path}
    elif network in ("h2", "http"):
        proxy["network"] = network
        options = {}
        if path:
            options["path"] = path
        if host:
            options["host"] = [host]
        if options:
            proxy["h2-opts"] = options
    return proxy


def _parse_trojan(link):
    parsed = urlsplit(link)
    password, host, port = unquote(parsed.username or ""), parsed.hostname, parsed.port
    if not (password and host and port):
        return None
    query = _query(parsed)
    proxy = {"name": _label(parsed, host), "type": "trojan", "server": host, "port": port, "password": password,
             "udp": True, "skip-cert-verify": query.get("allowInsecure", "0") in ("1", "true")}
    sni = query.get("sni") or query.get("peer")
    if sni:
        proxy["sni"] = sni
    network = query.get("type", "tcp").lower()
    if network == "ws":
        proxy["network"] = "ws"
        options = {}
        if query.get("path"):
            options["path"] = query["path"]
        if query.get("host"):
            options["headers"] = {"Host": query["host"]}
        if options:
            proxy["ws-opts"] = options
    elif network == "grpc":
        proxy["network"] = "grpc"
        proxy["grpc-opts"] = {"grpc-service-name": query.get("serviceName") or query.get("path", "")}
    return proxy


def _parse_vless(link):
    parsed = urlsplit(link)
    uuid, host, port = unquote(parsed.username or ""), parsed.hostname, parsed.port
    if not (uuid and host and port):
        return None
    query = _query(parsed)
    proxy = {"name": _label(parsed, host), "type": "vless", "server": host, "port": port, "uuid": uuid, "udp": True,
             "skip-cert-verify": query.get("allowInsecure", "0") in ("1", "true")}
    security = query.get("security", "none").lower()
    if security in ("tls", "reality"):
        proxy["tls"] = True
        sni = query.get("sni") or query.get("peer")
        if sni:
            proxy["servername"] = sni
        if query.get("fp"):
            proxy["client-fingerprint"] = query["fp"]
    if security == "reality":
        if not query.get("pbk"):
            return None
        proxy["reality-opts"] = {"public-key": query["pbk"], "short-id": query.get("sid", "")}
    network = query.get("type", "tcp").lower()
    if network == "ws":
        proxy["network"] = "ws"
        options = {}
        if query.get("path"):
            options["path"] = query["path"]
        if query.get("host"):
            options["headers"] = {"Host": query["host"]}
        if options:
            proxy["ws-opts"] = options
    elif network == "grpc":
        proxy["network"] = "grpc"
        proxy["grpc-opts"] = {"grpc-service-name": query.get("serviceName", "")}
    return proxy


def _parse_hy2(link):
    parsed = urlsplit(link)
    password, host, port = unquote(parsed.username or ""), parsed.hostname, parsed.port
    if not (password and host and port):
        return None
    query = _query(parsed)
    proxy = {"name": _label(parsed, host), "type": "hysteria2", "server": host, "port": port, "password": password,
             "skip-cert-verify": query.get("insecure", "0") in ("1", "true")}
    sni = query.get("sni") or query.get("peer")
    if sni:
        proxy["sni"] = sni
    if query.get("obfs"):
        proxy["obfs"] = query["obfs"]
        proxy["obfs-password"] = query.get("obfs-password", "")
    return proxy


def _parse_tuic(link):
    parsed = urlsplit(link)
    uuid, password = unquote(parsed.username or ""), unquote(parsed.password or "")
    host, port = parsed.hostname, parsed.port
    if not (uuid and host and port):
        return None
    query = _query(parsed)
    proxy = {"name": _label(parsed, host), "type": "tuic", "server": host, "port": port, "uuid": uuid,
             "password": password, "udp": True, "skip-cert-verify": query.get("allow_insecure", "0") in ("1", "true")}
    if query.get("sni"):
        proxy["sni"] = query["sni"]
    if query.get("alpn"):
        proxy["alpn"] = [value for value in query["alpn"].split(",") if value]
    return proxy


PARSERS = {"ss": _parse_ss, "vmess": _parse_vmess, "trojan": _parse_trojan, "vless": _parse_vless,
           "hysteria2": _parse_hy2, "hy2": _parse_hy2, "tuic": _parse_tuic}
RESERVED = {"DIRECT", "PROXY", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}


def convert_nodes(text):
    if LINK.search(text):
        decoded = text
    else:
        try:
            decoded = _b64(text)
        except (ValueError, binascii.Error, UnicodeError):
            return None
    proxies = []
    for line in decoded.splitlines():
        line = line.strip()
        if "://" not in line:
            continue
        parser = PARSERS.get(line.split("://", 1)[0].lower())
        if not parser:
            continue
        try:
            proxy = parser(line)
        except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
            proxy = None
        if proxy and proxy.get("server") and proxy.get("port"):
            proxies.append(proxy)
    if not proxies:
        return None
    seen, names = {}, []
    for proxy in proxies:
        base = str(proxy.get("name") or "节点").strip() or "节点"
        count = seen.get(base, 0) + 1
        seen[base] = count
        name = base if count == 1 else f"{base} {count}"
        while name in RESERVED:
            name += " ·"
        proxy["name"] = name
        names.append(name)
    config = {"proxies": proxies,
              "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": [*names, "DIRECT"]}],
              "rules": ["MATCH,PROXY"]}
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=1000)


LINK = re.compile(r"(?m)^\s*(?:ss|ssr|vmess|vless|trojan|hysteria2?|hy2|tuic)://")


def node_list(text):
    if LINK.search(text):
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 32 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return False
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_")
        decoded = raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return bool(re.search(r"(?:^|\n)\s*(?:ss|ssr|vmess|vless|trojan|hysteria2?|tuic)://", decoded))


async def checked(text, identity):
    if node_list(text):
        converted = convert_nodes(text)
        if not converted:
            raise HTTPException(422, "订阅返回的是 Base64 节点列表，但没有识别出可用节点；请改用机场的 Clash / Mihomo 订阅链接")
        text = converted
    if re.match(r"\s*<(!doctype|html)", text[:128], re.I):
        raise HTTPException(422, "订阅地址返回了网页，可能需要登录或链接已失效")
    try:
        parsed = yaml.safe_load(text)
        if broker.socket_path():
            if not isinstance(parsed, dict):
                raise ValueError("invalid profile")
            await broker.request("POST", "/engine/validate", json={"source": parsed}, timeout=45)
            return json.dumps(parsed, ensure_ascii=False)
        contents = normalize(parsed, identity)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        raise HTTPException(422, "配置内容无效，请检查 YAML 格式") from error
    engine = require_engine()
    private_dir(HOME)
    staged = HOME / "candidate.json"
    replace(staged, contents)
    try:
        code, _, _ = await run(engine, "-t", "-d", str(HOME), "-f", str(staged), timeout=35)
        if code:
            raise HTTPException(422, "Mihomo 配置校验失败，请检查节点和规则")
    finally:
        staged.unlink(missing_ok=True)
    return contents


async def apply(contents):
    if broker.socket_path():
        await broker.change("apply", json.loads(contents))
        return
    previous = CONFIG.read_text() if CONFIG.exists() else normalize({})
    live = running()
    if live:
        await stop()
    replace(CONFIG, contents)
    if live:
        try:
            await start()
        except HTTPException:
            replace(CONFIG, previous)
            await start()
            raise


@router.get("/profiles")
async def profiles():
    async with LOCK:
        data = library()
        return {"active": data["active"], "items": [{
            "id": identity, "name": row["name"], "subscription": bool(row["url"]),
            "updated": row["updated"], "usage": row.get("usage", {}), "active": data["active"] == identity,
        } for identity, row in data["items"].items()]}


@router.post("/profiles")
async def add_profile(form: Profile):
    name = form.name.strip()
    if not name:
        raise HTTPException(422, "请输入配置名称")
    url = source_url(form.url)
    text, usage = await download(url) if url else (form.text, {})
    if not text or not text.strip():
        raise HTTPException(422, "请提供 Clash / Mihomo YAML")
    async with LOCK:
        data = library()
        identity = uuid.uuid4().hex
        await checked(text, identity)
        data["items"][identity] = {"name": name, "source": text, "url": url, "usage": usage, "updated": time.time()}
        save_library(data)
    return {"id": identity}


@router.get("/profiles/{identity}")
async def get_profile(identity: str):
    async with LOCK:
        row = find(library(), identity)
        return Response(json.dumps({"name": row["name"], "text": row["source"], "subscription": bool(row["url"])},
                                   ensure_ascii=False), media_type="application/json", headers={"Cache-Control":"no-store"})


@router.get("/profiles/{identity}/download")
async def export_profile(identity: str):
    async with LOCK:
        row = find(library(), identity)
        filename = quote(row["name"] + ".yaml", safe="")
        return Response(row["source"], media_type="application/yaml", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Cache-Control":"no-store",
        })


@router.put("/profiles/{identity}")
async def edit_profile(identity: str, form: Profile):
    name = form.name.strip()
    if not name:
        raise HTTPException(422, "请输入配置名称")
    url = source_url(form.url)
    async with LOCK:
        data = library()
        row = find(data, identity)
        if form.text is not None and form.text != row["source"]:
            contents = await checked(form.text, identity)
            if data["active"] == identity:
                await apply(contents)
            row["source"] = form.text
        if form.url is not None:
            row["url"] = url
            row["usage"] = {}
        row["name"] = name
        row["updated"] = time.time()
        save_library(data)
    return {"ok": True}


@router.post("/profiles/{identity}/activate")
async def activate(identity: str):
    async with LOCK:
        data = library()
        row = find(data, identity)
        await apply(await checked(row["source"], identity))
        data["active"] = identity
        save_library(data)
    return {"ok": True}


@router.post("/profiles/{identity}/refresh")
async def refresh_profile(identity: str):
    async with LOCK:
        data = library()
        row = find(data, identity)
        if not row["url"]:
            raise HTTPException(400, "此配置没有订阅地址")
        text, usage = await download(row["url"])
        contents = await checked(text, identity)
        if data["active"] == identity:
            await apply(contents)
        row.update(source=text, usage=usage, updated=time.time())
        save_library(data)
    return {"ok": True}


@router.delete("/profiles/{identity}")
async def delete_profile(identity: str):
    async with LOCK:
        data = library()
        find(data, identity)
        if data["active"] == identity:
            enabled = (await broker.state())["enabled"] if broker.socket_path() else running()
            if enabled:
                raise HTTPException(409, "请先切换配置或停止 Mihomo")
            if broker.socket_path():
                await apply("{}")
            else:
                replace(CONFIG, normalize({}))
            data["active"] = None
        del data["items"][identity]
        save_library(data)
    return {"ok": True}


@router.post("/restart")
async def restart():
    if broker.socket_path():
        async with LOCK:
            await broker.change("restart")
        return {"ok": True}
    if broker.migration_pending():
        raise HTTPException(409, "请先迁移 Mac 上已有的 Mihomo 服务")
    async with LOCK:
        await stop()
        await start()
        replace(ENABLED, "1\n")
    return {"ok": True}


@router.put("/mode")
async def mode(form: Mode):
    async with LOCK:
        await control("PATCH", "/configs", json={"mode": form.mode})
        if not broker.socket_path():
            current = read_json(CONFIG, {})
            replace(CONFIG, json.dumps({**current, "mode": form.mode}, ensure_ascii=False))
    return {"ok": True}


@router.put("/selection")
async def select(form: Choice):
    async with LOCK:
        groups = (await snapshot())["groups"]
        group = next((item for item in groups if item["name"] == form.group and item["type"] in SELECTABLE), None)
        if not group or (form.node is not None and form.node not in group["nodes"]):
            raise HTTPException(422, "代理组或节点无效")
        path = "/proxies/" + quote(form.group, safe="")
        if form.node is None:
            if group["type"] == "Selector":
                raise HTTPException(422, "手动选择组不支持恢复自动")
            await control("DELETE", path)
        else:
            await control("PUT", path, json={"name": form.node})
    return {"ok": True}


@router.post("/delay")
async def delay(form: Delay):
    if not broker.socket_path() and not running():
        raise HTTPException(409, "请先启动 Mihomo")
    async with DELAY_SLOTS:
        try:
            data = await control("GET", "/proxies/" + quote(form.name, safe="") + "/delay",
                                 params={"url":"https://www.gstatic.com/generate_204","timeout":4500}, timeout=6)
            return {"delay": data.get("delay")}
        except HTTPException as error:
            if error.status_code in (409, 502):
                return {"delay": None}
            raise


@router.get("/providers")
async def providers():
    data = await control("GET", "/providers/proxies")
    return [{"name": name, "type": item.get("vehicleType"), "updated": item.get("updatedAt"),
             "nodes": len(item.get("proxies") or [])}
            for name, item in data["providers"].items()]


@router.put("/providers")
async def update_provider(form: Delay):
    async with LOCK:
        await control("PUT", "/providers/proxies/" + quote(form.name, safe=""), timeout=25)
    return {"ok": True}


@router.post("/providers/check")
async def check_provider(form: Delay):
    await control("GET", "/providers/proxies/" + quote(form.name, safe="") + "/healthcheck", timeout=25)
    return {"ok": True}


@router.post("/engine")
async def install_engine():
    if broker.socket_path():
        raise HTTPException(409, "请在主机维护独立服务的 Mihomo 内核")
    if broker.migration_pending():
        raise HTTPException(409, "请先迁移 Mac 上已有的 Mihomo 服务")
    arch = {"x86_64": "amd64-v1", "amd64": "amd64-v1", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    system = platform.system().lower()
    if not arch or system not in ("linux", "darwin"):
        raise HTTPException(400, "此设备架构需要手动安装 Mihomo")
    github = "https://github.com/MetaCubeX/mihomo/releases/download/"
    async with LOCK:
        try:
            async with asyncio.timeout(150):
                async with httpx.AsyncClient(timeout=25, follow_redirects=True, trust_env=False) as client:
                    release = (await client.get("https://api.github.com/repos/MetaCubeX/mihomo/releases/latest")).raise_for_status().json()
                    tag = release["tag_name"]
                    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
                        raise ValueError("invalid version")
                    installed = executable()
                    if installed:
                        code, out, _ = await run(installed, "-v")
                        if not code and tag in out.split():
                            return {"version": tag, "updated": False}
                    name = f"mihomo-{system}-{arch}-{tag}.gz"
                    file = next(asset for asset in release["assets"] if asset["name"] == name)
                    contents = bytearray()
                    async with client.stream("GET", f"{github}{tag}/{name}") as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            contents.extend(chunk)
                            if len(contents) > 40_000_000:
                                raise ValueError("asset too large")
            if file.get("digest") != "sha256:" + hashlib.sha256(contents).hexdigest():
                raise ValueError("invalid release checksum")
            unpacked = gzip.decompress(contents)
        except (httpx.HTTPError, ValueError, KeyError, TimeoutError, StopIteration, OSError, EOFError) as error:
            raise HTTPException(502, "无法下载官方 Mihomo 内核，请检查 GitHub 连通性") from error
        private_dir(MANAGED_ENGINE.parent)
        staged = MANAGED_ENGINE.with_suffix(".new")
        backup = MANAGED_ENGINE.with_suffix(".previous")
        try:
            staged.write_bytes(unpacked)
            staged.chmod(0o700)
            code, _, _ = await run(str(staged), "-v")
            if code:
                raise HTTPException(502, "无法运行下载的 Mihomo 内核")
            live = running()
            if live:
                await stop()
            if MANAGED_ENGINE.exists():
                MANAGED_ENGINE.replace(backup)
            staged.replace(MANAGED_ENGINE)
            if live:
                try:
                    await start()
                except HTTPException:
                    MANAGED_ENGINE.unlink()
                    if backup.exists():
                        backup.replace(MANAGED_ENGINE)
                    await start()
                    raise
            backup.unlink(missing_ok=True)
        finally:
            staged.unlink(missing_ok=True)
    return {"version": tag, "updated": True}


@router.get("/rules")
async def rules():
    rows = (await control("GET", "/rules"))["rules"]
    return {"total": len(rows), "items": rows[:2000]}


@router.get("/connections")
async def connections():
    data = await control("GET", "/connections")
    rows = data.get("connections") or []
    rows.sort(key=lambda row: row.get("upload", 0) + row.get("download", 0), reverse=True)
    return {"total": len(rows), "upload": data.get("uploadTotal", 0), "download": data.get("downloadTotal", 0),
            "items": rows[:100]}


@router.delete("/connections")
async def close_all():
    await control("DELETE", "/connections")
    return {"ok": True}


@router.delete("/connections/{identity}")
async def close_connection(identity: uuid.UUID):
    await control("DELETE", "/connections/" + str(identity))
    return {"ok": True}


@router.get("/logs")
async def logs():
    if broker.socket_path():
        return await broker.request("GET", "/engine/logs")
    path = HOME / "mihomo.log"
    try:
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 64 * 1024))
            text = log.read().decode(errors="replace")
        return {"text": "\n".join(text.splitlines()[-200:])}
    except FileNotFoundError:
        return {"text": ""}


@router.post("/check/{target}")
async def check(target: str):
    if target not in CHECKS:
        raise HTTPException(404)
    if broker.socket_path():
        proxy = f"http://127.0.0.1:{(await broker.state())['mixed_port']}"
    elif running():
        proxy = PROXY
    else:
        raise HTTPException(409, "请先启动 Mihomo")
    try:
        async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=10) as client:
            response = await client.get(CHECKS[target])
        return {"ok": response.is_success or response.status_code == 401, "status": response.status_code}
    except httpx.RequestError:
        return {"ok": False, "status": None}
