"""Install a user-run, independent Mihomo service on Linux."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
import yaml

from . import mihomo
from .runtime import HOME, ROOT, STATE, private_dir, read_json, replace
from .service import systemd_quote


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check, capture_output=True)


def main():
    if not sys.platform.startswith("linux"):
        sys.exit("macOS 的系统 TUN 请使用 migrate_mihomo_macos.py")
    service = "server-console-mihomo.service"
    console = "server-console.service"
    unit = HOME / ".config/systemd/user" / service
    if unit.exists():
        systemctl("enable", "--now", service)
        print("Mihomo 独立服务已安装。")
        return
    binary = mihomo.require_engine()
    runtime = private_dir(STATE / "mihomo-service")
    daemon = runtime / "daemon.py"
    replace(daemon, (ROOT / "scripts/mihomo_daemon.py").read_text(), mode=0o700)
    cache = mihomo.HOME / "cache.db"
    current = read_json(mihomo.CONFIG, {})
    library = mihomo.library()
    active = library["items"].get(library["active"])
    source = yaml.safe_load(active["source"]) if active else {key: current[key] for key in mihomo.PROFILE_KEYS if key in current}
    enabled = mihomo.ENABLED.exists()
    replace(runtime / "state.json", json.dumps({
        "source": source, "enabled": enabled, "mode": current.get("mode", source.get("mode", "rule")),
    }))
    path = mihomo.broker.socket_path()
    if path is not None:
        sys.exit("已发现其他独立 Mihomo 服务，请先检查配置")
    socket_path = f"{os.getenv('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}/server-console-mihomo.sock"
    argv = ["/usr/bin/python3", "-I", str(daemon), "--home", str(runtime), "--binary",
            binary, "--socket", socket_path, "--uid", str(os.getuid())]
    subprocess.run([*argv, "--validate-only"], check=True)
    console_was_running = systemctl("is-active", console, check=False).returncode == 0
    if console_was_running:
        systemctl("stop", console)
    try:
        if cache.is_file():
            shutil.copy2(cache, runtime / "cache.db")
        private_dir(unit.parent)
        replace(unit, f"""[Unit]
Description=Server Console Mihomo engine

[Service]
ExecStart={' '.join(systemd_quote(argument) for argument in argv)}
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
""")
        systemctl("daemon-reload")
        systemctl("enable", "--now", service)
        deadline = time.monotonic() + 15
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket_path), timeout=2) as client:
            while time.monotonic() < deadline:
                try:
                    state = client.get("http://localhost/engine/state").raise_for_status().json()
                    if state["enabled"] == enabled and state["ready"] == enabled:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("独立 Mihomo 服务启动超时")
    except Exception:
        systemctl("disable", "--now", service, check=False)
        unit.unlink(missing_ok=True)
        systemctl("daemon-reload", check=False)
        if console_was_running:
            systemctl("start", console)
        raise
    if console_was_running:
        systemctl("start", console)
    print("已启用独立 Mihomo systemd 用户服务。")


if __name__ == "__main__":
    main()
