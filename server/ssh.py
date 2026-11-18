import asyncio
import glob
import re
import shlex
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .runtime import HOME, private_dir, replace, require
from .terminal import create_session

router = APIRouter()
CONFIG = HOME / ".ssh/config"
LOCK = asyncio.Lock()
ALIAS = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
OPTIONS = {"hostname": "HostName", "user": "User", "port": "Port"}


class Host(BaseModel):
    alias: str = Field(max_length=128)
    hostname: str = Field(max_length=253)
    user: str = Field(default="", max_length=100)
    port: int | None = Field(default=None, ge=1, le=65535)


def directive(line):
    match = re.match(r"^\s*([a-zA-Z]+)(?:\s*=\s*|\s+)(.*)$", line)
    if not match:
        return "", []
    try:
        return match[1].lower(), shlex.split(match[2], comments=True)
    except ValueError:
        return "", []


def read_lines(path):
    try:
        with path.open(newline="") as file:
            return file.read().splitlines(keepends=True)
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as error:
        raise HTTPException(409, str(error)) from None


def blocks(lines):
    boundaries = [i for i, line in enumerate(lines) if directive(line)[0] in {"host", "match"}]
    return zip(boundaries, [*boundaries[1:], len(lines)])


def entries(path=None, ancestry=frozenset()):
    path = path or CONFIG
    resolved = path.resolve()
    if resolved in ancestry or len(ancestry) >= 12:
        return
    lines = read_lines(path)
    root = path == CONFIG
    for start, end in blocks(lines):
        key, aliases = directive(lines[start])
        if key != "host":
            continue
        options = {}
        for line in lines[start + 1:end]:
            name, values = directive(line)
            if name in OPTIONS and values:
                options.setdefault(name, values[0])
        for alias in aliases:
            if ALIAS.fullmatch(alias):
                yield {"alias": alias, **options, "editable": root and len(aliases) == 1,
                       "source": str(path)}
    for line in lines:
        key, patterns = directive(line)
        if key == "include":
            for pattern in patterns:
                target = Path(pattern).expanduser()
                if not target.is_absolute():
                    target = CONFIG.parent / target
                for name in sorted(glob.glob(str(target))):
                    yield from entries(Path(name), ancestry | {resolved})


@router.get("/api/ssh")
def hosts():
    items = {}
    for item in entries():
        items.setdefault(item["alias"].lower(), item)
    return list(items.values())


def validate(host):
    if not ALIAS.fullmatch(host.alias):
        raise HTTPException(400, "别名仅支持字母、数字、点、下划线和连字符")
    if not re.fullmatch(r"[A-Za-z0-9_:][A-Za-z0-9_.:%-]*", host.hostname):
        raise HTTPException(400, "请输入有效的 SSH 主机名或 IP")
    if host.user and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host.user):
        raise HTTPException(400, "请输入有效的 SSH 用户名")


def update_file(old, host):
    if host:
        validate(host)
        if host.alias.lower() != (old or "").lower() and any(
            item["alias"].lower() == host.alias.lower() for item in entries()
        ):
            raise HTTPException(409, "该 SSH 别名已存在")
    path = CONFIG.resolve()
    lines = read_lines(path)
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    values = {"hostname": host.hostname, "user": host.user, "port": host.port} if host else {}
    if old:
        for start, end in blocks(lines):
            key, aliases = directive(lines[start])
            if key == "host" and len(aliases) == 1 and aliases[0].lower() == old.lower():
                if host:
                    body = lines[start + 1:end]
                    tail = []
                    while body and (not body[-1].strip() or body[-1].lstrip().startswith("#")):
                        tail.insert(0, body.pop())
                    updated = []
                    for line in body:
                        key, _ = directive(line)
                        if key in OPTIONS:
                            value = values.pop(key, None)
                            if value:
                                updated.append(f"    {OPTIONS[key]} {value}" + newline)
                        else:
                            updated.append(line if line.endswith("\n") else line + newline)
                    lines[start:end] = [f"Host {host.alias}" + newline, *updated,
                                        *(f"    {OPTIONS[key]} {value}" + newline for key, value in values.items() if value),
                                        *tail]
                else:
                    tail = []
                    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
                        end -= 1
                        tail.insert(0, lines[end])
                    del lines[start:end]
                break
        else:
            raise HTTPException(409, "只能修改主配置中独立的 Host 条目")
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += newline
        # Insert before Host/Match defaults so their first-value-wins options cannot override this entry.
        start = next((i for i, line in enumerate(lines) if directive(line)[0] in {"host", "match"}), len(lines))
        lines[start:start] = [f"Host {host.alias}" + newline,
                              *(f"    {OPTIONS[key]} {value}" + newline for key, value in values.items() if value),
                              newline]
    private_dir(CONFIG.parent)
    if path.exists():
        replace(CONFIG.parent / "config.server-console.bak", "".join(read_lines(path)))
    replace(path, "".join(lines))
    return {"ok": True}


@router.post("/api/ssh")
async def add(host: Host):
    async with LOCK:
        return update_file(None, host)


@router.put("/api/ssh/{alias}")
async def edit(alias: str, host: Host):
    async with LOCK:
        return update_file(alias, host)


@router.delete("/api/ssh/{alias}")
async def delete(alias: str):
    async with LOCK:
        return update_file(alias, None)


@router.post("/api/ssh/{alias}/connect")
async def connect(alias: str):
    if not ALIAS.fullmatch(alias) or not any(item["alias"] == alias for item in hosts()):
        raise HTTPException(404, "SSH 别名不存在")
    return await create_session("ssh", HOME, alias, [require("ssh"), alias])
