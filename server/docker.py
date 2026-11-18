import asyncio
import json
import re

from fastapi import APIRouter, HTTPException

from .runtime import HOME, binary, require, run
from .terminal import create_session

router = APIRouter()


def json_lines(output):
    return [json.loads(line) for line in output.splitlines() if line.strip()]


@router.get("/api/docker")
async def docker_status():
    docker = binary("docker")
    if not docker:
        return {"state": "missing", "detail": "当前用户的 PATH 中没有 Docker CLI。", "containers": []}
    code, out, err = await run(docker, "info", "--format", "{{.ServerVersion}}")
    if code:
        detail = (err or out).strip()
        denied = "permission denied" in detail.lower() or "access denied" in detail.lower()
        return {"state": "denied" if denied else "offline", "detail": detail, "containers": []}
    version = out.strip().splitlines()[0] if out.strip() else ""
    code, out, err = await run(docker, "ps", "-a", "--no-trunc", "--format", "{{json .}}")
    if code:
        raise HTTPException(502, (err or out).strip())
    return {"state": "ready", "version": version, "containers": json_lines(out)}


@router.get("/api/docker/stats")
async def stats():
    code, out, err = await run(require("docker"), "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}", timeout=10)
    if code:
        raise HTTPException(502, (err or out).strip())
    return {row["ID"]: row for row in json_lines(out)}


async def docker_command(container, *arguments, timeout=20):
    if not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise HTTPException(400, "容器 ID 无效")
    code, out, err = await run(require("docker"), arguments[0], *arguments[1:], container, timeout=timeout)
    if code:
        raise HTTPException(502, (err or out).strip())
    return out, err


@router.get("/api/docker/{container}/logs")
async def logs(container: str):
    out, err = await docker_command(container, "logs", "--tail", "300", "--timestamps")
    return {"output": (out + err)[-200000:]}


@router.post("/api/docker/{container}/{action}")
async def action(container: str, action: str):
    if action in {"start", "stop", "restart"}:
        await docker_command(container, action, timeout=40)
        return {"ok": True}
    if action == "terminal":
        out, _ = await docker_command(container, "inspect", "--format", "{{.Name}}|{{.State.Running}}")
        name, _, state = out.strip().partition("|")
        if state != "true":
            raise HTTPException(409, "请先启动容器")
        docker = require("docker")
        code, _, _ = await run(docker, "exec", container, "/bin/bash", "-c", "true")
        shell = "/bin/bash" if code == 0 else "/bin/sh"
        return await create_session("docker", HOME, name.lstrip("/"),
                                    [docker, "exec", "-it", container, shell])
    raise HTTPException(404)
