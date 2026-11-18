"""Transfer an existing Mac Mihomo TUN service to a Server Console LaunchDaemon."""
import http.client
import json
import os
import plistlib
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

OLD_LABEL = "local.openremotehub.proxy"
LABEL = "local.server-console.mihomo"
OLD_SOCKET = Path("/var/run/openremotehub-proxy.sock")
SOCKET = Path("/var/run/server-console-mihomo.sock")
OLD_HOME = Path("/Library/Application Support/OpenRemoteHubProxy")
HOME = Path("/Library/Application Support/ServerConsoleMihomo")
PLISTS = Path("/Library/LaunchDaemons")


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=8)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect(str(self.path))


def api(path, endpoint, method="GET", data=None):
    conn = UnixConnection(path)
    try:
        conn.request(method, endpoint, body=json.dumps(data) if data is not None else None,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        result = json.loads(response.read() or "{}")
        if response.status >= 400:
            raise RuntimeError("Mihomo 后台拒绝操作")
        return result
    finally:
        conn.close()


def launch(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True)


def atomic(path, contents, mode, uid=0, gid=0):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        target = Path(file.name)
        try:
            file.write(contents)
            file.flush()
            os.fchmod(file.fileno(), mode)
            os.fchown(file.fileno(), uid, gid)
            os.replace(target, path)
        finally:
            target.unlink(missing_ok=True)


def wait_ready(path, seconds=20):
    expires = time.monotonic() + seconds
    while time.monotonic() < expires:
        try:
            status = api(path, "/engine/state")
            if status["ready"]:
                return status
        except (OSError, RuntimeError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError("新的 Mihomo 后台未就绪")


def main():
    if sys.platform != "darwin" or os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
        raise RuntimeError("请由当前 Mac 管理员通过 sudo 运行迁移命令")
    owner = pwd.getpwnam(os.environ["SUDO_USER"])
    profile_dir = Path(owner.pw_dir) / "Library/Application Support/OpenRemoteHub/.runtime/proxy"
    library_file = Path(owner.pw_dir) / ".config/server-console/mihomo/profiles.json"
    new_plist = PLISTS / (LABEL + ".plist")
    try:
        if api(SOCKET, "/engine/state")["ready"]:
            print("Server Console Mihomo 已在运行。")
            return
    except (OSError, RuntimeError, ValueError):
        pass

    state = api(OLD_SOCKET, "/orh/state")
    if not state["ready"]:
        raise RuntimeError("旧 Mihomo 无法访问，迁移尚未开始")
    old_plist = PLISTS / (OLD_LABEL + ".plist")
    old = plistlib.loads(old_plist.read_bytes())
    if old["Label"] != OLD_LABEL or str(OLD_HOME / "mihomo") not in old["ProgramArguments"]:
        raise RuntimeError("旧启动服务路径与预期不符")

    import fcntl
    with (profile_dir / "library.lock").open("a") as lock:
        os.fchown(lock.fileno(), owner.pw_uid, owner.pw_gid)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = json.loads((profile_dir / "profiles.json").read_text())
        library = json.loads(library_file.read_text()) if library_file.exists() else {"items": {}, "active": None}
        for row in source["profiles"]:
            library["items"][row["id"]] = {
                "name": row["name"], "source": json.dumps(row["source"], ensure_ascii=False, indent=2),
                "url": row.get("url") or None, "updated": row["updated"], "usage": row.get("usage", {}),
            }
        library["active"] = source["active"]
        old_selection = api(OLD_SOCKET, "/proxies")["proxies"]
        runner = Path(__file__).resolve().with_name("mihomo_daemon.py")

        HOME.mkdir(mode=0o700, exist_ok=True)
        if HOME.is_symlink() or HOME.stat().st_uid != 0:
            raise RuntimeError("新的系统服务目录需要由 root 持有")
        engine = HOME / "engine"
        shutil.copytree(OLD_HOME / "engine", engine, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns("*.log", "candidate.json", "config.json", "cache.db"))
        shutil.copy2(OLD_HOME / "mihomo", HOME / "mihomo")
        atomic(HOME / "mihomo_daemon.py", runner.read_bytes(), 0o700)
        argv = ["/usr/bin/python3", "-I", str(HOME / "mihomo_daemon.py"), "--home", str(engine), "--binary",
                str(HOME / "mihomo"), "--socket", str(SOCKET), "--uid", str(owner.pw_uid),
                "--system-tun", "--mixed-port", str(state["mixed_port"])]
        subprocess.run([*argv, "--validate-only"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atomic(new_plist, plistlib.dumps({
            "Label": LABEL, "ProgramArguments": argv, "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 5, "Umask": 0o077, "StandardOutPath": str(HOME / "runner.log"),
            "StandardErrorPath": str(HOME / "runner.log"),
        }), 0o644)

        disabled_old = False
        try:
            launch("disable", f"system/{OLD_LABEL}")
            disabled_old = True
            launch("bootout", f"system/{OLD_LABEL}")
            deadline = time.monotonic() + 12
            while OLD_SOCKET.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            old_cache = OLD_HOME / "engine/cache.db"
            if old_cache.is_file():
                shutil.copy2(old_cache, engine / "cache.db")
            launch("enable", f"system/{LABEL}")
            launch("bootstrap", "system", str(new_plist))
            new = wait_ready(SOCKET)
            actual = api(SOCKET, "/configs")
            if bool(actual["tun"]["enable"]) != state["enabled"] or new["config_revision"] != state["config_revision"]:
                raise RuntimeError("新服务的代理配置或 TUN 状态不匹配")
            latest = api(SOCKET, "/proxies")["proxies"]
            for name, before in old_selection.items():
                node = before.get("now") if before.get("type") == "Selector" else before.get("fixed")
                group = latest.get(name, {})
                if node and node in group.get("all", []) and node != group.get("now"):
                    api(SOCKET, "/proxies/" + quote(name, safe=""), "PUT", {"name": node})

            # These folders normally exist after installation, or may be new on the Mac.
            for parent in [Path(owner.pw_dir) / ".config", library_file.parent.parent, library_file.parent]:
                if not parent.exists():
                    parent.mkdir(mode=0o700)
                    os.chown(parent, owner.pw_uid, owner.pw_gid)
            atomic(library_file, json.dumps(library, ensure_ascii=False).encode(), 0o600, owner.pw_uid, owner.pw_gid)
            print(f"迁移完成。Mihomo PID {new['pid']}，端口 {new['mixed_port']}，已复制 {len(source['profiles'])} 份配置。")
        except Exception as error:
            launch("disable", f"system/{LABEL}", check=False)
            launch("bootout", f"system/{LABEL}", check=False)
            new_plist.unlink(missing_ok=True)
            if disabled_old:
                launch("enable", f"system/{OLD_LABEL}", check=False)
                if launch("print", f"system/{OLD_LABEL}", check=False).returncode:
                    launch("bootstrap", "system", str(old_plist))
            raise RuntimeError("迁移失败，已尝试恢复旧 Mihomo。") from error


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
