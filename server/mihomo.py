import asyncio
import hashlib
import json
from urllib.parse import quote, urlsplit

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .runtime import ENV, STATE, binary, private_dir, replace, require, run

router = APIRouter(prefix="/api/mihomo")
HOME = STATE / "mihomo"
CONFIG = HOME / "config.json"
CONTROL = HOME / "control.sock"
ENABLED = HOME / "enabled"
PROXY_PORT = 17890
PROXY = f"http://127.0.0.1:{PROXY_PORT}"
LOCK = asyncio.Lock()
process = None

PROFILE_KEYS = (
    "proxies", "proxy-groups", "proxy-providers", "rules", "rule-providers", "mode",
    "ipv6", "unified-delay", "tcp-concurrent", "global-client-fingerprint",
)
CHECKS = {
    "openai": "https://api.openai.com/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
}


class Profile(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


class Choice(BaseModel):
    group: str = Field(min_length=1, max_length=200)
    node: str = Field(min_length=1, max_length=200)


def normalize(source):
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
            filename = hashlib.sha256(f"{kind}:{name}".encode()).hexdigest()[:24]
            sanitized[name] = {**item, "path": f"providers/{filename}.yaml"}
        if sanitized:
            config[kind] = sanitized
    config.setdefault("rules", ["MATCH,DIRECT"])
    config.update({
        "mixed-port": PROXY_PORT, "allow-lan": False, "bind-address": "127.0.0.1",
        "external-controller-unix": str(CONTROL), "tun": {"enable": False},
        "dns": {"enable": False}, "profile": {"store-selected": True},
        "log-level": "warning",
    })
    return json.dumps(config, ensure_ascii=False, indent=2)


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
    executable = require("mihomo")
    if running():
        return
    private_dir(HOME)
    if not CONFIG.exists():
        replace(CONFIG, normalize({}))
    CONTROL.unlink(missing_ok=True)
    with (HOME / "mihomo.log").open("ab") as log:
        process = await asyncio.create_subprocess_exec(
            executable, "-d", str(HOME), "-f", str(CONFIG),
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
    if ENABLED.is_file() and binary("mihomo"):
        try:
            async with LOCK:
                await start()
        except (HTTPException, OSError):
            pass


async def snapshot():
    try:
        async with controller() as client:
            version, config, proxies = await asyncio.gather(
                client.get("/version"), client.get("/configs"), client.get("/proxies"),
            )
            for response in (version, config, proxies):
                response.raise_for_status()
        groups = [{
            "name": name, "current": group.get("now"), "nodes": group["all"],
        } for name, group in proxies.json()["proxies"].items()
            if group.get("type") == "Selector" and "all" in group
            and (name != "GLOBAL" or config.json().get("mode") == "global")]
        return {"version": version.json()["version"], "mode": config.json().get("mode"), "groups": groups}
    except (httpx.HTTPError, KeyError, ValueError) as error:
        raise HTTPException(502, "Mihomo 控制接口暂不可用") from error


@router.get("")
async def status():
    available = bool(binary("mihomo"))
    state = "running" if running() else "error" if ENABLED.exists() else "stopped"
    return {
        "available": available, "state": state, "port": PROXY_PORT,
        "configured": CONFIG.is_file(),
        **(await snapshot() if state == "running" else {"groups": []}),
    }


@router.post("/start")
async def enable():
    async with LOCK:
        await start()
        replace(ENABLED, "1\n")
    return {"ok": True}


@router.post("/stop")
async def disable():
    async with LOCK:
        ENABLED.unlink(missing_ok=True)
        await stop()
    return {"ok": True}


@router.post("/profile")
async def profile(form: Profile):
    executable = require("mihomo")
    try:
        contents = normalize(yaml.safe_load(form.text))
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        raise HTTPException(422, "配置内容无效，请检查 YAML 格式") from error
    async with LOCK:
        private_dir(HOME)
        staged = HOME / "candidate.json"
        replace(staged, contents)
        try:
            code, _, _ = await run(executable, "-t", "-d", str(HOME), "-f", str(staged), timeout=35)
            if code:
                raise HTTPException(422, "Mihomo 配置校验失败，请检查节点和规则")
            previous = CONFIG.read_text() if CONFIG.exists() else None
            was_running = running()
            if was_running:
                await stop()
            staged.replace(CONFIG)
            if was_running:
                try:
                    await start()
                except HTTPException:
                    replace(CONFIG, previous)
                    await start()
                    raise
        finally:
            staged.unlink(missing_ok=True)
    return {"ok": True}


@router.put("/selection")
async def select(form: Choice):
    async with LOCK:
        groups = (await snapshot())["groups"]
        if not any(group["name"] == form.group and form.node in group["nodes"] for group in groups):
            raise HTTPException(422, "代理组或节点无效")
        try:
            async with controller() as client:
                response = await client.put("/proxies/" + quote(form.group, safe=""), json={"name": form.node})
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise HTTPException(502, "切换节点失败") from error
    return {"ok": True}


@router.post("/check/{target}")
async def check(target: str):
    if target not in CHECKS:
        raise HTTPException(404)
    if not running():
        raise HTTPException(409, "请先启动 Mihomo")
    try:
        async with httpx.AsyncClient(proxy=PROXY, trust_env=False, timeout=10) as client:
            response = await client.get(CHECKS[target])
        return {"ok": response.is_success or response.status_code == 401, "status": response.status_code}
    except httpx.RequestError:
        return {"ok": False, "status": None}
