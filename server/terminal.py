import asyncio
import fcntl
import json
import os
import pty
import re
import shutil
import signal
import struct
import sys
import tempfile
import termios
import uuid

import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .runtime import ENV, STATE, ai_env, directory, private_dir, read_json, replace, require, run

router = APIRouter()
MANAGED = re.compile(r"sc-[a-f0-9]{12}")
LOCK = asyncio.Lock()
ZELLIJ_CONFIG = '''
default_layout "compact"
default_mode "locked"
pane_frames false
show_startup_tips false
show_release_notes false
copy_command ""
web_sharing "disabled"
session_serialization false
'''


class NewSession(BaseModel):
    name: str = Field(default="", max_length=100)
    cwd: str = Field(default="~", max_length=4096)
    resume: str = Field(default="", max_length=100)


def zellij():
    path = private_dir(STATE) / "zellij.kdl"
    if not path.exists() or path.read_text() != ZELLIJ_CONFIG:
        replace(path, ZELLIJ_CONFIG)
    return [require("zellij"), "--config", str(path)]


def metadata():
    return read_json(STATE / "terminals.json", {})


def save_sessions(values):
    replace(private_dir(STATE) / "terminals.json", json.dumps(values, ensure_ascii=False, indent=2))


async def active_sessions():
    code, out, err = await run(*zellij(), "list-sessions", "--no-formatting")
    if code and "No active zellij sessions" not in (out + err):
        raise HTTPException(503, (err or out).strip() or "无法读取 Zellij 会话")
    return [match[1] for line in out.splitlines()
            if (match := re.match(r"^([^/-][^/]*?) \[Created ", line)) and "[EXITED" not in line]


@router.get("/api/terminal")
async def sessions():
    items = metadata()
    return [items.get(name, {"id": name, "name": name, "kind": "shell", "cwd": "", "managed": False})
            for name in await active_sessions()]


async def create_session(kind, cwd, name, command):
    session = "sc-" + uuid.uuid4().hex[:12]
    async with LOCK:
        # Wait for the launcher itself. A background session must not keep a
        # captured output pipe open while its first pane is running.
        with tempfile.TemporaryFile() as output:
            try:
                launcher = await asyncio.create_subprocess_exec(
                    *zellij(), "attach", "--create-background", session, "--", *command,
                    cwd=cwd, env=ai_env() if kind == "codex" else ENV, stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=output,
                )
            except OSError as error:
                raise HTTPException(502, "无法启动 Zellij") from error
            try:
                code = await asyncio.wait_for(launcher.wait(), 8)
            except (asyncio.TimeoutError, asyncio.CancelledError) as error:
                if launcher.returncode is None:
                    try:
                        launcher.kill()
                    except ProcessLookupError:
                        pass
                    await launcher.wait()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise HTTPException(504, "创建 Zellij 会话超时") from error
            if code:
                output.seek(0)
                detail = output.read(4096).decode(errors="replace").strip()
                raise HTTPException(502, detail or "无法创建 Zellij 会话")
        item = {"id": session, "name": name, "kind": kind, "cwd": str(cwd), "managed": True}
        items = metadata()
        items[session] = item
        save_sessions(items)
        return item


@router.post("/api/terminal/{kind}")
async def new_session(kind: str, form: NewSession):
    cwd = directory(form.cwd)
    if kind == "shell":
        import pwd
        login_shell = pwd.getpwuid(os.getuid()).pw_shell
        command = [shutil.which(login_shell) or "/bin/sh", "-l"]
    elif kind == "codex":
        command = [require("codex"), "--no-alt-screen"]
        if form.resume:
            modes = {"picker": ["--all"], "project": [], "last": ["--last"]}
            if form.resume not in modes:
                try:
                    resume_id = str(uuid.UUID(form.resume))
                except ValueError:
                    raise HTTPException(400, "请输入有效的 Codex 会话 UUID") from None
                arguments = [resume_id]
            else:
                arguments = modes[form.resume]
            command = [command[0], "resume", "--no-alt-screen", *arguments]
    else:
        raise HTTPException(404)
    names = {"shell": "Shell", "codex": "Codex"}
    return await create_session(kind, cwd, form.name.strip() or names[kind], command)


def session_servers(session):
    marker = "/" + session
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = process.info["cmdline"] or []
            if "--server" not in command:
                continue
            index = command.index("--server")
            socket = command[index + 1] if index + 1 < len(command) else ""
            if socket.endswith(marker):
                found.append((process.info["pid"], socket))
        except (psutil.Error, ValueError):
            continue
    return found


def force_stop(servers):
    for pid, socket in servers:
        try:
            process = psutil.Process(pid)
            if process.is_running():
                process.kill()
                process.wait(1)
        except psutil.Error:
            pass
        try:
            os.unlink(socket)
        except OSError:
            pass


@router.delete("/api/terminal/{session}")
async def stop_session(session: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise HTTPException(400, "无效的会话名")
    async with LOCK:
        # list-sessions can block on one unresponsive session, so do not consult
        # it here. Kill, then force-kill a server that ignored the request.
        await run(*zellij(), "kill-session", session, timeout=6)
        servers = await asyncio.to_thread(session_servers, session)
        if servers:
            await asyncio.sleep(0.8)
            await asyncio.to_thread(force_stop, servers)
        if MANAGED.fullmatch(session):
            items = metadata()
            items.pop(session, None)
            save_sessions(items)
    return {"ok": True}


def resize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def wait_fd(fd, writing=False):
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    add = loop.add_writer if writing else loop.add_reader
    remove = loop.remove_writer if writing else loop.remove_reader

    def wake():
        if not ready.done():
            ready.set_result(None)

    add(fd, wake)
    try:
        await ready
    finally:
        remove(fd)


async def bridge(ws, fd):
    async def output():
        while True:
            await wait_fd(fd)
            try:
                chunk = os.read(fd, 32768)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            await ws.send_bytes(chunk)

    async def input_stream():
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text"):
                try:
                    data = json.loads(message["text"])
                    cols, rows = int(data["cols"]), int(data["rows"])
                    resize(fd, max(2, min(1000, cols)), max(2, min(500, rows)))
                except (KeyError, TypeError, ValueError):
                    continue
            elif message.get("bytes"):
                pending = memoryview(message["bytes"])
                while pending:
                    try:
                        pending = pending[os.write(fd, pending):]
                    except BlockingIOError:
                        await wait_fd(fd, writing=True)

    tasks = [asyncio.create_task(output()), asyncio.create_task(input_stream())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/ws/terminal/{session}")
async def socket(ws: WebSocket, session: str):
    await ws.accept()
    try:
        if session not in await active_sessions():
            await ws.close(4404, "会话已结束")
            return
        # The client options are merged into the existing session's runtime options.
        # Apply the Locked preset only when creating a console-managed session.
        argv = [require("zellij"), "--config", os.devnull, "attach", session,
                "options", "--on-force-close", "detach"]
        cols = max(2, min(1000, int(ws.query_params.get("cols", "100"))))
        rows = max(2, min(500, int(ws.query_params.get("rows", "30"))))
    except (HTTPException, ValueError):
        await ws.close(4400, "无法连接会话")
        return

    master, slave = pty.openpty()
    process = None
    try:
        resize(slave, cols, rows)
        os.set_blocking(master, False)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "server.pty_exec", *argv,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env={**ENV, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
        )
        os.close(slave)
        slave = None
        await bridge(ws, master)
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 1)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
