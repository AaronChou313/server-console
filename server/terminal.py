import asyncio
import fcntl
import json
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import uuid
from functools import lru_cache
from pathlib import Path

import psutil
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .runtime import ENV, STATE, ai_env, directory, private_dir, read_json, replace, require, run

router = APIRouter()
MANAGED = re.compile(r"sc-[a-f0-9]{12}")
LOCK = asyncio.Lock()
STOPPING = set()
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


def valid_session_name(name):
    return bool(name.strip()) and name not in {".", ".."} and not name.startswith("-") and (
        "/" not in name and all(char.isprintable() for char in name)
    )


@lru_cache(maxsize=1)
def socket_directory():
    if override := ENV.get("ZELLIJ_SOCKET_DIR"):
        base = Path(override).absolute()
    elif sys.platform == "linux" and ENV.get("XDG_RUNTIME_DIR", "").startswith("/"):
        base = Path(ENV["XDG_RUNTIME_DIR"]) / "zellij"
    else:
        temp = ENV.get("TMPDIR")
        if not temp and sys.platform == "darwin":
            temp = subprocess.check_output(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], text=True).strip()
        base = Path(temp or "/tmp") / f"zellij-{os.getuid()}"
    # Zellij 0.45 and 0.46 share this on-disk client/server contract.
    return (base / "contract_version_1").resolve()


def session_servers():
    directory = socket_directory()
    executable_names = {"zellij", Path(require("zellij")).resolve().name}
    servers = {}
    for process in psutil.process_iter(["cmdline", "uids"]):
        try:
            credentials = process.info["uids"]
            if credentials is None or credentials.real != os.getuid():
                continue
            args = process.info["cmdline"]
            if not args or len(args) < 3 or args[1] != "--server" or Path(process.exe()).name not in executable_names:
                continue
            socket = Path(args[2])
            if socket.parent.resolve() == directory and socket.is_socket() and valid_session_name(socket.name):
                servers[socket.name] = (process, socket)
        except (OSError, psutil.Error):
            continue
    return servers


async def active_sessions():
    code, out, err = await run(*zellij(), "list-sessions", "--no-formatting", timeout=3)
    if code == 124:
        # One wedged server can block the CLI's whole socket scan.
        return sorted(await asyncio.to_thread(session_servers))
    if code and "No active zellij sessions" not in (out + err):
        raise HTTPException(503, (err or out).strip() or "无法读取 Zellij 会话")
    return [match[1] for line in out.splitlines()
            if (match := re.fullmatch(r"(.+) \[Created [^\]]+\]\s*(?:\(current\))?", line))
            and valid_session_name(match[1])]


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


def wait_for_exit(process, timeout):
    try:
        process.wait(timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True


def force_stop(process, socket):
    try:
        original = socket.lstat()
    except FileNotFoundError:
        original = None
    try:
        process.kill()
    except psutil.NoSuchProcess:
        pass
    if not wait_for_exit(process, 2):
        return False
    # The name might already have been reused by a new Zellij instance.
    if original:
        try:
            if os.path.samestat(original, socket.lstat()):
                socket.unlink()
        except FileNotFoundError:
            pass
    return True


@router.delete("/api/terminal/{session}")
async def stop_session(session: str, force: bool = False):
    if not valid_session_name(session):
        raise HTTPException(400, "无效的会话名")
    async with LOCK:
        STOPPING.add(session)
        try:
            target = (await asyncio.to_thread(session_servers)).get(session)
            if not target:
                # A healthy CLI can still stop sessions we cannot identify for signal-based recovery.
                if force:
                    raise HTTPException(404, "未找到可确认身份的 Zellij 会话")
                code, out, err = await run(*zellij(), "kill-session", session, timeout=3)
                if code:
                    if (f"No session named {json.dumps(session, ensure_ascii=False)} found." in out):
                        raise HTTPException(404, "会话已结束")
                    raise HTTPException(502, (err or out).strip() or "无法结束 Zellij 会话")
            else:
                process, socket = target
                try:
                    if force:
                        stopped = await asyncio.to_thread(force_stop, process, socket)
                    else:
                        await run(*zellij(), "kill-session", session, timeout=3)
                        stopped = await asyncio.to_thread(wait_for_exit, process, 2)
                except (OSError, psutil.Error) as error:
                    raise HTTPException(502, "无法结束此 Zellij 会话，请检查主机权限") from error
                if not stopped:
                    raise HTTPException(409, "Zellij 尚未结束此会话。确认需要终止其中的进程后，可以强制结束。")
            if MANAGED.fullmatch(session):
                items = metadata()
                items.pop(session, None)
                save_sessions(items)
        finally:
            STOPPING.discard(session)
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
            if message.get("text") == "ping":
                await ws.send_text("pong")
            elif message.get("text"):
                try:
                    data = json.loads(message["text"])
                    cols, rows = int(data["cols"]), int(data["rows"])
                    resize(fd, max(2, min(1000, cols)), max(2, min(500, rows)))
                except (KeyError, OverflowError, TypeError, ValueError):
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
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return tasks[0] in done
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
    except HTTPException:
        await ws.close(1013, "暂时无法读取会话")
        return
    except ValueError:
        await ws.close(4400, "终端尺寸无效")
        return

    try:
        master, slave = pty.openpty()
    except OSError:
        await ws.close(1013, "暂时无法分配终端，请稍后重试")
        return
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
        try:
            await asyncio.wait_for(wait_fd(master), 10)
        except asyncio.TimeoutError:
            await ws.close(4400, "Zellij 会话未响应，请检查会话后重连")
            return
        if await bridge(ws, master):
            try:
                result = await asyncio.wait_for(process.wait(), 0.5)
            except asyncio.TimeoutError:
                await ws.close(1013, "终端传输已中断")
                return
            if session in STOPPING or not (socket_directory() / session).is_socket():
                await ws.close(4404, "会话已结束")
            elif result != 0:
                await ws.close(4400, "Zellij 连接失败，请检查终端输出")
            else:
                await ws.close(4402, "已从会话分离，可手动重连")
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
