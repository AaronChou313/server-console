import argparse
import ipaddress
import os
import plistlib
import socket
from pathlib import Path

from .runtime import PATH, ROOT, STATE, config, private_dir, replace


def systemd_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def main():
    parser = argparse.ArgumentParser(description="安装 Server Console 用户服务")
    parser.add_argument("manager", choices=["launchd", "systemd"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9786)
    args = parser.parse_args()
    if not config().get("password_hash"):
        parser.error("请先执行 ./console password")
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("请先执行 ./console setup")
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        parser.error("--host 需要填写本机接口的 IP 地址")
    if address.is_unspecified or not 1024 <= args.port <= 65535:
        parser.error("请选择具体的监听地址及 1024–65535 之间的端口")
    try:
        with socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET) as sock:
            sock.bind((args.host, 0))
    except OSError as error:
        parser.error(f"当前主机无法绑定 {args.host}：{error}")
    private_dir(STATE)
    args.file.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "PATH": PATH, "SERVER_CONSOLE_STATE": str(STATE), "SERVER_CONSOLE_HOST": args.host,
        "SERVER_CONSOLE_PORT": str(args.port),
    }
    overrides = ["SERVER_CONSOLE_" + tool for tool in ("CODEX", "DSH", "DOCKER", "TAILSCALE", "ZELLIJ", "SSH", "MIHOMO")]
    for name in ("CODEX_HOME", "DOCKER_HOST", "DOCKER_CONTEXT",
                 "SERVER_CONSOLE_MIHOMO_BROKER", "SERVER_CONSOLE_TOOL_PROXY", *overrides):
        if name in os.environ:
            values[name] = os.environ[name]
    if args.manager == "launchd":
        log = STATE / "service.log"
        log.touch(mode=0o600)
        replace(args.file, plistlib.dumps({
            "Label": "local.server-console", "ProgramArguments": [str(ROOT / "console"), "run"],
            "WorkingDirectory": str(ROOT), "EnvironmentVariables": values,
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5,
            "StandardOutPath": str(log), "StandardErrorPath": str(log), "AbandonProcessGroup": True,
        }).decode())
    else:
        env = "\n".join("Environment=" + systemd_quote(key + "=" + value) for key, value in values.items())
        replace(args.file, f"""[Unit]
Description=Server Console
After=network-online.target

[Service]
ExecStart={systemd_quote(str(ROOT / "console"))} run
WorkingDirectory={str(ROOT).replace("%", "%%")}
{env}
Restart=on-failure
RestartSec=5
# Leave Zellij session servers alive when stopping the console.
KillMode=process
UMask=0077

[Install]
WantedBy=default.target
""")
    display = f"[{args.host}]" if address.version == 6 else args.host
    print(f"Server Console → http://{display}:{args.port}")


if __name__ == "__main__":
    main()
