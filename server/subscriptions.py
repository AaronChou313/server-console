"""Decode Clash subscriptions and common standalone share links."""
import base64
import binascii
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import yaml
from fastapi import HTTPException


def _b64(value):
    raw = "".join(value.split())
    return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True).decode("utf-8")


def _query(parsed):
    return {key: values[-1] for key, values in parse_qs(parsed.query).items()}


def _label(parsed, fallback):
    return unquote(parsed.fragment).strip() or fallback


def _parse_ss(link):
    parsed = urlsplit(link)
    if "@" in parsed.netloc:
        host, port = parsed.hostname, parsed.port
        username = unquote(parsed.username or "")
        credential = (f"{username}:{unquote(parsed.password)}" if parsed.password is not None
                      else _b64(username))
        plugin = _query(parsed).get("plugin", "")
    else:
        # Legacy ss://BASE64(method:password@host:port)#name
        credential, separator, endpoint = _b64(parsed.netloc).rpartition("@")
        if not separator:
            return None
        target = urlsplit("//" + endpoint)
        host, port = target.hostname, target.port
        plugin = ""
    method, separator, password = credential.partition(":")
    if not (host and port and method and separator and password):
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
    if security not in ("none", "tls", "reality"):
        return None
    if query.get("flow"):
        proxy["flow"] = query["flow"]
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
    elif network not in ("tcp", "raw"):
        return None
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


LINK = re.compile(r"(?im)^\s*(?:ss|ssr|vmess|vless|trojan|hysteria2?|hy2|tuic)://")
WRAPPED_YAML = re.compile(r"(?m)^\s*(?:proxies|proxy-providers|proxy-groups|rules|mixed-port)\s*:")


def links_to_yaml(text):
    proxies = []
    names = set(RESERVED)
    next_suffix = {}
    for line in text.splitlines():
        scheme, _, _ = line.strip().partition("://")
        parser = PARSERS.get(scheme.lower())
        if not parser:
            continue
        try:
            proxy = parser(line.strip())
        except (ValueError, KeyError, TypeError):
            continue
        if not proxy:
            continue
        base = proxy["name"] or "节点"
        name = base
        index = next_suffix.get(base, 2)
        while name in names:
            name = f"{base} {index}"
            index += 1
        next_suffix[base] = index
        names.add(name)
        proxies.append({**proxy, "name": name})
    if not proxies:
        raise HTTPException(422, "无法识别此节点列表，请使用订阅方提供的 Clash / Mihomo 链接")
    config = {
        "proxies": proxies,
        "proxy-groups": [{"name": "PROXY", "type": "select",
                          "proxies": [*[proxy["name"] for proxy in proxies], "DIRECT"]}],
        "rules": ["MATCH,PROXY"],
    }
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def profile_text(text):
    """Return editable YAML, preserving the rules of full profiles."""
    text = text.lstrip("\ufeff")
    if re.match(r"\s*<(!doctype|html)", text[:128], re.I):
        raise HTTPException(422, "订阅地址返回了网页，请检查链接是否有效或是否需要登录")
    if LINK.search(text):
        return links_to_yaml(text)
    compact = "".join(text.split())
    if len(compact) >= 16 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        try:
            decoded = _b64(compact)
        except (binascii.Error, ValueError):
            return text
        if LINK.search(decoded):
            return links_to_yaml(decoded)
        if WRAPPED_YAML.search(decoded):
            return decoded.lstrip("\ufeff")
    return text
