"""Prepare the Geo data that the profile actually references."""
import asyncio
import os
import re
import tempfile
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

RELEASE = "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/"
CDN = "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/"
ASSETS = {"mmdb": "geoip.metadb", "geoip": "geoip.dat",
          "geosite": "geosite.dat", "asn": "GeoLite2-ASN.mmdb"}
MMDB_ALIASES = ("country.mmdb", "geoip.db")
LIMIT = 40 * 1024 * 1024


def assets_for(source):
    dns = source.get("dns")
    dns = dns if isinstance(dns, dict) else {}
    fallback = dns.get("fallback-filter")
    fallback = fallback if isinstance(fallback, dict) else {}
    rules = source.get("rules")
    rules = rules if isinstance(rules, list) else []
    matches = {match.upper() for row in rules if isinstance(row, str)
               for match in re.findall(r"(?:^|[,(])\s*(GEOIP|GEOSITE|IP-ASN|SRC-IP-ASN)\s*,", row, re.I)}
    needs_geoip = "GEOIP" in matches or (dns.get("fallback") and fallback.get("geoip", True))
    needs_geosite = "GEOSITE" in matches or (dns.get("fallback") and fallback.get("geosite"))
    for key in ("nameserver-policy", "proxy-server-nameserver-policy", "fake-ip-filter"):
        values = dns.get(key)
        if isinstance(values, (dict, list)) and any(
            isinstance(item, str) and item.split(":", 1)[0].lstrip("+.").lower() == "geosite" for item in values
        ):
            needs_geosite = True

    keys = []
    if needs_geoip:
        keys.append("geoip" if source.get("geodata-mode") is True else "mmdb")
    if needs_geosite:
        keys.append("geosite")
    if matches & {"IP-ASN", "SRC-IP-ASN"}:
        keys.append("asn")
    return keys


def asset_urls(source, kind):
    urls = source.get("geox-url")
    if isinstance(urls, dict) and (url := urls.get(kind)):
        if isinstance(url, str) and urlsplit(url).scheme in ("http", "https"):
            return [url]
        return []
    name = ASSETS[kind]
    if mirror := os.getenv("SERVER_CONSOLE_GEO_MIRROR"):
        return [mirror.rstrip("/") + "/" + name]
    prefix = os.getenv("SERVER_CONSOLE_GITHUB_MIRROR", "")
    return [github_url(RELEASE + name, prefix), CDN + name]


def github_url(url, prefix):
    return prefix.rstrip("/") + "/" + url if prefix else url


def ready(path):
    return path.is_file() and path.stat().st_size > 1024


async def download_asset(client, urls, target):
    for url in urls:
        try:
            async with asyncio.timeout(30), client.stream("GET", url) as response:
                response.raise_for_status()
                if "text/html" in response.headers.get("content-type", "").lower():
                    continue
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
                    temp = target.with_name(os.path.basename(file.name))
                    try:
                        size = 0
                        async for chunk in response.aiter_bytes(256 * 1024):
                            size += len(chunk)
                            if size > LIMIT:
                                raise ValueError("oversized Geo data")
                            file.write(chunk)
                        if size <= 1024:
                            continue
                        file.close()
                        os.replace(temp, target)
                        return True
                    finally:
                        temp.unlink(missing_ok=True)
        except (httpx.HTTPError, OSError, TimeoutError, ValueError):
            continue
    return False


async def ensure_geo(source, directory):
    if directory is None:
        return
    missing = {}
    for kind in assets_for(source):
        target = directory / ASSETS[kind]
        candidates = (target, *(directory / name for name in MMDB_ALIASES)) if kind == "mmdb" else (target,)
        if not any(ready(path) for path in candidates):
            # Replace a partial file under the name Mihomo will resolve first.
            missing[kind] = next((path for path in candidates if path.is_file()), target)
    if not missing:
        return
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), follow_redirects=True, max_redirects=5) as client:
        for kind, target in missing.items():
            if not await download_asset(client, asset_urls(source, kind), target):
                raise HTTPException(502, f"无法下载 {target.name}，请检查网络或配置中的 geox-url；"
                                         "也可设置 SERVER_CONSOLE_GEO_MIRROR 或预置 Geo 数据")
