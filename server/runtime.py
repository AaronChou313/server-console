import asyncio
import getpass
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
STATE = Path(os.getenv("SERVER_CONSOLE_STATE", str(HOME / ".config/server-console"))).expanduser()
PATH = os.pathsep.join(dict.fromkeys([
    *filter(None, os.environ.get("PATH", "").split(os.pathsep)),
    str(HOME / ".local/share/server-console/node24/bin"), str(HOME / ".local/bin"), str(HOME / "bin"), "/opt/homebrew/bin", "/usr/local/bin",
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
]))
ENV = {**os.environ, "PATH": PATH}
ENV.pop("ZELLIJ", None)
ENV.pop("ZELLIJ_SESSION_NAME", None)


def ai_env():
    proxy = os.getenv("SERVER_CONSOLE_TOOL_PROXY")
    if not proxy:
        return ENV
    return {**ENV, "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
            "http_proxy": proxy, "https_proxy": proxy,
            "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
            "NODE_USE_ENV_PROXY": "1"}


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def replace(path, content, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            os.fchmod(file.fileno(), mode)
            file.write(content.encode())
            file.close()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def config():
    return read_json(STATE / "config.json", {})


def write_config(values):
    replace(private_dir(STATE) / "config.json", json.dumps({**config(), **values}, indent=2) + "\n")


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def set_password(password):
    if not 4 <= len(password) <= 256:
        raise ValueError("密码长度须为 4–256 个字符")
    salt = secrets.token_hex(16)
    write_config({"salt": salt, "password_hash": password_hash(password, salt)})


def verify_password(password):
    values = config()
    return "salt" in values and len(password) <= 256 and secrets.compare_digest(
        password_hash(password, values["salt"]), values["password_hash"]
    )


def binary(name):
    override = os.getenv("SERVER_CONSOLE_" + name.upper())
    if override:
        return shutil.which(override, path=PATH)
    result = shutil.which(name, path=PATH)
    if not result and name == "dsh" and sys.platform == "darwin":
        return shutil.which("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")
    return result


def require(name):
    result = binary(name)
    if not result:
        raise HTTPException(503, f"找不到 {name}。请安装后重试。")
    return result


async def run(*args, cwd=None, timeout=8, env=None):
    try:
        process = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, env=env or ENV, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        return 127, "", str(error)
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if isinstance(error, asyncio.CancelledError):
            raise
        return 124, "", "命令超时"
    return process.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def directory(value):
    try:
        target = Path(value or "~").expanduser().resolve(strict=True)
        if target.is_dir() and os.access(target, os.R_OK | os.X_OK):
            return target
    except (OSError, RuntimeError, ValueError):
        pass
    raise HTTPException(400, "项目目录不存在或当前用户无法访问")


if __name__ == "__main__":
    if sys.argv[1:] == ["password"]:
        first = getpass.getpass("Server Console 管理密码：")
        if first != getpass.getpass("确认密码："):
            sys.exit("两次密码不一致")
        try:
            set_password(first)
            (STATE / "initial-password").unlink(missing_ok=True)
        except ValueError as error:
            sys.exit(str(error))
    elif sys.argv[1:] == ["generate-password"]:
        if config().get("password_hash"):
            sys.exit("管理员密码已设置")
        password = secrets.token_urlsafe(24)
        set_password(password)
        replace(STATE / "initial-password", password + "\n")
    else:
        sys.exit("用法：python -m server.runtime password")
