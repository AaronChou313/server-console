# Server Console

把一台 Mac 或 Linux 服务器变成浏览器工作台。一个 FastAPI 服务同时提供 React 前端、API 和 WebSocket，登录后即可在浏览器里使用终端、Docker、SSH、网络诊断、Mihomo 代理，以及 Codex 和 DeepSeek Harness。

- **一个服务**：`./console` 负责安装、运行、常驻和更新。
- **普通账户即可**：默认安装到当前用户目录，不需要 root；控制台继承该账户的文件权限和工具配置。
- **按需扩展**：基础功能只依赖 Python、Node.js 和 Zellij；Docker、Tailscale、Codex、DSH、Mihomo 都可以后装。

> **第一次使用**：从「快速开始」开始；不想手敲命令，直接跳到「交给本机 Agent 部署」。

---

## 快速开始

> 前提：一台 Linux（amd64/arm64）或 macOS 服务器，能用普通账户 SSH 登录；服务器能访问 GitHub、Node.js、PyPI 和 npm（受限见「离线或受限网络」）。

### 1. 连接服务器并安装

在**你自己的电脑**上打开终端（Windows 用 PowerShell），把 `user@server` 换成你的账户和地址：

```bash
ssh -L 9786:127.0.0.1:9786 user@server

git clone https://github.com/AaronChou313/server-console.git
cd server-console
./console bootstrap
./console generate-password
./console install
```

`bootstrap` 会把 Node.js 24、Zellij 0.45.1、Python 依赖装进当前用户目录，并构建前端；`install` 安装用户级常驻服务。两者都不需要 sudo。

### 2. 打开控制台

保持 SSH 连接，在本机浏览器打开：

**http://127.0.0.1:9786**

首次密码保存在服务器的 `~/.config/server-console/initial-password`，可以这样查看：

```bash
cat ~/.config/server-console/initial-password
```

它与 SSH 密码独立，支持 4–256 个字符（包括四位数字）。想自己设置密码时用 `./console password`（交互式）；改密码后执行 `./console restart` 会让已有网页登录失效。

部署完成后可以快速确认：

```bash
./console status                                  # 服务应为 active/running
curl -fsS http://127.0.0.1:9786/api/auth          # 应返回 JSON
```

### 3. 常见调整

- **`install` 提示无法连接 systemd 用户服务**：确认是直接 SSH 登录到目标账户（不是 `su`、容器或非登录 shell），或先用 `./console run` 前台运行。
- **本机 9786 已被占用**：给 `install` 加 `--port 19786`，SSH 隧道同步改成 `-L 19786:127.0.0.1:19786`。
- **希望退出 SSH 后服务继续运行**：在 Linux 上执行 `sudo loginctl enable-linger "$USER"`（需要 sudo）。没有 sudo 时，用户服务会在注销后停止。
- **服务器是 macOS**：步骤相同，常驻服务使用 launchd，部署账户登录后自动启动。

### 4. 直接访问（可选）

默认只监听回环地址，通过 SSH 隧道访问最安全。如果服务器和客户端在同一可信内网，或已经组好 Tailscale：

```bash
# 内网直连
./console install --host 192.168.1.50 --port 9786

# Tailscale（只对 Tailnet 开放）
./console install --host "$(tailscale ip -4)" --port 9786
```

之后浏览器打开 `http://<host>:9786`。直连模式是明文 HTTP，且控制台登录等于服务器账户的 Shell，请只在可信网络使用。需要浏览器剪贴板、全屏等增强能力时，建议套一层 HTTPS 反向代理。

---

## 交给本机 Agent 部署

不想手动敲命令时，把下面整段复制给自己电脑上的 Agent（Claude、Codex、DSH 等），先把三个占位符换成你的连接信息。

```text
你是一名熟悉 Linux/macOS 的运维助手。请通过 SSH 连接到我的服务器，并在服务器上部署 Server Console（一个浏览器工作台）。全程使用我提供的普通账户操作，不要切换到 root，也不要修改系统级配置。

连接信息：
- 主机：<SERVER_HOST>
- SSH 端口：<SSH_PORT>（默认 22）
- 用户名：<SSH_USER>
- 认证：优先使用我本机已有的 SSH 密钥/配置；如果需要密码或 sudo 密码，先问我。

部署要求：
1. 先探测环境并告诉我结果：uname -s、uname -m、/etc/os-release、python3 版本、git/curl/tar/gzip 是否可用、systemctl --user 是否可用、9786 端口是否被占用。
2. 代码放在 $HOME/server-console。优先 git clone https://github.com/AaronChou313/server-console.git。如果服务器访问 GitHub 失败，不要卡住：在本地把项目打包（git bundle 或 tar，排除 .venv、node_modules、frontend/dist），用 scp 传到服务器解压后继续。
3. 在代码目录执行 ./console bootstrap。只有我明确需要 AI/代理时才加 --with-ai / --with-mihomo；不确定就先问我。bootstrap 会把 Node、Zellij、uv 等装到我的用户目录，不需要 root。
4. 如果还没有控制台密码，执行 ./console generate-password，并告诉我密码文件位置（~/.config/server-console/initial-password）。不要把密码重复打印到日志或报告里。
5. 执行 ./console install（默认 127.0.0.1:9786，通过 SSH 隧道访问）。如果我要求直连或 Tailscale，再改用 ./console install --host <IP>。如果我的账户不能使用 systemd 用户服务，改用 ./console run 放在 tmux/nohup 中，并明确告诉我这个限制。
6. 如果 sudo 可用（先用 sudo -n true 检测），执行 sudo loginctl enable-linger "$USER"；如果不可用，跳过并告诉我登出/重启后服务会停止。
7. 验证：./console status；在服务器上 curl -fsS http://127.0.0.1:9786/api/auth 应返回 JSON（如果用了 --host/--port，换成对应地址和端口）；查看 ./console logs 最近几行确认没有报错。
8. 在本机启动 SSH 隧道（后台）：ssh -N -L 9786:127.0.0.1:9786 -p <SSH_PORT> <SSH_USER>@<SERVER_HOST>，然后告诉我浏览器打开 http://127.0.0.1:9786。如果本机 9786 被占用，换一个本地端口并给出对应 URL。
9. 最后给我一份简短报告：访问地址、密码文件位置、已安装组件（基础/AI/Mihomo）、没有完成的步骤及原因，以及后续维护命令（status/restart/logs/更新）。

安全约束：
- 不要修改防火墙、SSH 配置或系统级服务；不要使用 --host 0.0.0.0，除非我明确要求。
- 不要把密码、token、订阅链接写进日志或聊天记录；不要执行来源不明的脚本。
- 如果某一步需要 root 而我无法 sudo，停下来告诉我需要什么，不要尝试提权。
```

> Agent 如果无法在本机后台保持 SSH 隧道，让它把隧道命令交给你手动执行即可。直连或 Tailscale 部署时，把报告里的访问地址换成对应主机地址。

---

## 手动部署

### 准备

**服务器**：Linux amd64/arm64（常见 glibc 发行版）或 macOS；一个带家目录的普通账户；至少 `git`、`curl`、`tar`、`gzip`。缺少这些系统工具时，用系统包管理器安装（可能需要 sudo）：

```bash
# Debian / Ubuntu
sudo apt install -y git curl tar gzip ca-certificates

# Fedora / RHEL
sudo dnf install -y git curl tar gzip ca-certificates
```

**权限**：控制台以你的账户运行，不需要 root；sudo 只用于安装系统包和可选的 linger。

**运行环境**：`bootstrap` 会自动准备 Python 3.11+、Node.js 24、Zellij 0.45+，不需要预装。它默认写入：

- Node.js：`~/.local/share/server-console/node24`
- Zellij 和用户级命令：`~/.local/bin`
- Python：优先用系统 3.11+；否则通过 uv 安装 3.12 到 `.venv`

**可选组件**：Docker、Tailscale、Codex、DeepSeek Harness、Mihomo。基础控制台不依赖它们。

### 1. 获取代码

```bash
git clone https://github.com/AaronChou313/server-console.git ~/server-console
cd ~/server-console
```

服务器访问 GitHub 失败时，在自己电脑上打包再上传：

```bash
# 本机
git clone https://github.com/AaronChou313/server-console.git
tar --exclude=.venv --exclude=frontend/node_modules --exclude=frontend/dist \
  -czf server-console.tgz server-console
scp server-console.tgz user@server:~

# 服务器
tar xzf ~/server-console.tgz && cd ~/server-console
```

也可以使用 `git bundle`：本机 `git bundle create server-console.bundle main`，上传后执行 `git clone server-console.bundle server-console`。

### 2. 一键准备

```bash
./console bootstrap
```

按需追加：

```bash
# 同时安装 Codex 和 DeepSeek Harness
./console bootstrap --with-ai

# 同时安装 Mihomo；Linux 还会安装独立后台服务
./console bootstrap --with-mihomo

# 全部安装
./console bootstrap --with-ai --with-mihomo
```

`bootstrap` 会按需下载，已有可用版本时跳过。完成后会提示执行 `./console password && ./console install`。

### 3. 设置密码

```bash
# 首次：生成随机密码并写入 ~/.config/server-console/initial-password
./console generate-password

# 或自己设置（交互式输入两次）
./console password
```

密码独立于 SSH，支持 4–256 个字符。`generate-password` 只在尚未设置密码时可用。

### 4. 安装并启动

```bash
# 默认：127.0.0.1:9786，配合 SSH 隧道
./console install

# 自定义端口（本机 9786 被占用时）
./console install --port 19786

# 直接监听内网或 Tailscale 地址
./console install --host 192.168.1.50 --port 9786
./console install --host "$(tailscale ip -4)" --port 9786
```

查看和验证：

```bash
./console status
curl -fsS http://127.0.0.1:9786/api/auth
```

如果暂时不想装常驻服务，或 `install` 提示 systemd 用户服务不可用：

```bash
./console run
```

需要后台运行时，把它放在 `tmux` 或 Zellij 会话里；但这不会在重启后自动恢复。

Linux 上希望退出 SSH 后继续运行：

```bash
sudo loginctl enable-linger "$USER"
```

### 5. 离线或受限网络

默认使用上游下载源。服务器无法访问其中某一项时，可按需指定自己能访问的镜像或代理。不要把这些变量写入全局 npm 或 pip 配置。

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `SERVER_CONSOLE_GITHUB_MIRROR` | GitHub URL 前缀，用于 Zellij、Mihomo 发行版和 uv 下载 | 直连 |
| `SERVER_CONSOLE_NODE_MIRROR` | 提供 `latest-v24.x` 的 Node.js 发行版根地址 | `https://nodejs.org/dist` |
| `SERVER_CONSOLE_UV_BASE` | 提供 `install.sh` 的 uv 安装器地址 | `https://astral.sh/uv` |
| `SERVER_CONSOLE_NPM_REGISTRY` | 前端依赖和 AI 工具的 npm registry | npm 自身配置 |
| `SERVER_CONSOLE_GEO_MIRROR` | Mihomo Geo 数据根地址，仅补齐缺失的文件 | MetaCubeX GitHub release，失败后尝试项目列出的 jsDelivr |
| `PIP_INDEX_URL` | PyPI 索引地址 | pip 自身配置 |

可以先装基础控制台，再按需添加 AI 和代理。若 GitHub Release 不通，可从另一台机器下载对应架构的运行时再上传。Node 解压到 `~/.local/share/server-console/node24` 时去掉压缩包顶层目录，Zellij 放到 `~/.local/bin/zellij`，Mihomo 可用 `SERVER_CONSOLE_MIHOMO=<绝对路径> ./console install-mihomo` 指向已有二进制。完全离线时还需要准备同架构的 Python 依赖、构建好的 `frontend/dist` 和所需工具。仅传代码无法完成离线部署。

Geo 数据镜像如需在常驻 Web 服务中生效，使用 `SERVER_CONSOLE_GEO_MIRROR=<地址> ./console install`，沿用原来的 `--host` 和 `--port` 参数。订阅如果提供了自定义 `geox-url`，优先使用其指定地址。

### 6. 更新

```bash
cd ~/server-console
git pull --ff-only
./console setup
./console restart
```

用 tar 包部署（没有 `.git`）时，重新上传覆盖后执行 `./console setup && ./console restart`。用户数据保留在 `~/.config/server-console/`；`frontend/dist/` 和 `.venv/` 是构建产物，不在版本控制中。

---

## 访问方式

| 方式 | 命令 | 浏览器地址 | 适合与注意 |
| --- | --- | --- | --- |
| SSH 隧道（推荐） | `ssh -L 9786:127.0.0.1:9786 user@server` | `http://127.0.0.1:9786` | 不开放端口，最安全；明文 HTTP，剪贴板 API 受限 |
| 内网直连 | `./console install --host <内网IP>` | `http://<内网IP>:9786` | 同一可信网段；需要防火墙放行 |
| Tailscale | `./console install --host "$(tailscale ip -4)"` | `http://<Tailnet IP>:9786` | 仅 Tailnet 内可访问，无需公网端口 |
| HTTPS 反向代理 | 由 Caddy/Nginx 转发到控制台 | `https://你的域名/` | 需要剪贴板 API、全屏等浏览器能力时使用 |

**SSH 隧道补充**：

- 必须经跳板机：`ssh -J user@bastion -L 9786:127.0.0.1:9786 user@server`。
- 服务器有多个网卡：`--host` 选客户端能到达的那个地址。
- 隧道断开后网页会断线；重新执行 SSH 命令并刷新即可，Zellij 会话仍在服务器上。

**HTTPS 反向代理**：保留原始 `Host`、可信的 `X-Forwarded-Proto` 和 WebSocket `Upgrade`。反向代理与控制台同机时，默认信任来自回环代理的转发协议。

Caddy 示例（自动处理 WebSocket 和转发头）：

```caddy
console.example.com {
    reverse_proxy 127.0.0.1:9786
}
```

---

## 可选组件

### AI 工具（Codex / DeepSeek Harness）

```bash
./console bootstrap --with-ai
./console restart
```

安装后打开「AI 工作台」即可使用。Codex 需要访问账号认证和模型端点：

```bash
export PATH="$HOME/.local/share/server-console/node24/bin:$HOME/.local/bin:$PATH"
codex login status
codex login --device-auth
```

- `--device-auth` 适合远程/无头服务器，不需要浏览器回调；按提示在自己的电脑上打开链接、输入代码。
- 如果账号未启用设备码认证，可用 `codex login --with-api-key` 从标准输入提供 API Key。
- 需要代理时，在终端里显式设置 `HTTPS_PROXY` / `HTTP_PROXY`，或使用下面的 `SERVER_CONSOLE_TOOL_PROXY`。

DSH 使用「DeepSeek Harness」提供的官方 Web 界面。首次使用可选以下方式。

1. **API Key**。进入 DSH 的「设置 → 模型」，选择 DeepSeek 提供方，保存自己的 API Key。DSH 进程需要能访问该提供方的端点。
2. **DeepSeek 账号**。在「登录与配置」中点击「准备账号登录」。按提示在打开浏览器的电脑上运行 SSH 转发命令，使用自己平时能登录服务器的 SSH 用户、IP、别名或跳板参数，然后打开临时回环地址。即使控制台本身经 SSH 转发到 localhost，也要使用这条单独的登录转发，因为 OAuth 回调必须到达 DSH 的原生端口。登录完成后点「刷新 DSH 实例」读取账户凭据。刷新会中断正在运行的 DSH 任务，请在任务结束后操作。

临时登录页只监听服务器回环地址，约 15 分钟后关闭，也可提前点「结束登录」。正常使用时服务器凭据由 DSH 原生配置管理。[DeepSeek 账号登录限制](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/account/deepseek-account/README.md)

### 为新建工具指定代理

如果希望工作台新建的 Codex / DSH 进程自动继承本机 Mihomo：

```bash
SERVER_CONSOLE_TOOL_PROXY=http://127.0.0.1:17890 ./console install
```

它只影响之后新启动的工具进程，已有终端会话不受影响。先确认「网络代理」里的代理端口和运行状态。

### Mihomo 网络代理

```bash
# 只安装内核
./console install-mihomo

# Linux：再安装可脱离控制台运行的独立后台
./console install-mihomo-service

# 或一步完成（Linux 会同时安装后台）
./console bootstrap --with-mihomo
```

- 代理默认监听 `127.0.0.1:17890`（HTTP / SOCKS5），独立后台通过私有 Unix socket 管理。
- 打开「网络代理 → 配置 → 导入配置」，可导入 Clash / Mihomo YAML、订阅链接、Base64 节点列表或常见纯节点订阅；转换后由 Mihomo 校验。
- 订阅返回 HTTP 429 时是服务商限流，等一段时间或更换订阅链接，不要连续重试。
- 配置引用 GEOIP / GEOSITE / 远程规则时，首次校验需要对应数据。无法访问 GitHub 时，预置 `country.mmdb`、`geoip.metadb`、`geosite.dat`，或改用可达的 `geox-url`。
- Linux 独立服务的数据目录是 `~/.config/server-console/mihomo-service/`，网页直接启动的内核使用 `~/.config/server-console/mihomo/`。

### Docker / Tailscale

- **Docker**：控制台沿用部署账户已能访问的 Docker Engine。先用 `docker ps` 验证权限，再使用 Docker 页面。不要为了省事把账户加入 docker 组或改用 root，除非你清楚这等同于高权限。
- **Tailscale**：可选。装好并登录后，用 `./console install --host "$(tailscale ip -4)"` 只对 Tailnet 开放。

---

## 功能

| 模块 | 操作 |
| --- | --- |
| 概览 | 查看 CPU、内存、用户磁盘卷、网络速率与运行时间。每 3 秒采样，趋势保留在当前浏览器内存中。 |
| 我的应用 | 自动发现本机监听并返回 HTML 的 Web 服务；点击直接打开，回环地址服务经控制台临时转发。 |
| 终端 | 新建项目 Shell，或连接已有的 Zellij 会话。浏览器关闭、刷新和 WebSocket 重连都会保留服务器进程。 |
| 代码编辑器 | 用 code-server 在浏览器打开主机目录，编辑并保存文件。 |
| Docker | 查看 Engine、容器、CPU 和内存，启动、停止、重启、查看日志或进入容器。 |
| SSH | 用系统 OpenSSH 发起连接。保存前在 `~/.ssh/config.server-console.bak` 留备份。 |
| 网络 | 查看接口、连接、Tailscale Peer 和链路；支持 Ping、TCP、Tailscale Ping、Netcheck。 |
| 网络代理 | 管理 Mihomo 内核、配置、订阅、节点测速、规则、连接和日志。 |
| AI 工作台 | 在终端使用 Codex CLI，在 DSH 页面使用 DeepSeek Harness 官方 Web 界面。 |
| 设置 | 在当前浏览器保存亮暗主题、终端字体与字号。 |

### 多设备发现与切换

在每台 Mac 或 Linux 上独立部署控制台。登录后点击顶部主机名，可以发现其他控制台、刷新列表或手动添加地址。切换会直接打开目标 URL，登录由目标控制台处理。

- **局域网**使用 mDNS/DNS-SD。让目标控制台监听局域网 IP，例如 `./console install --host 192.168.1.10`。监听回环或 Tailscale IP 的实例仍可发现同网段设备，但不会发布局域网地址。跨网段或组播受限时，可手动填写浏览器能访问的地址。
- **Tailscale**在已连接时探测在线 Peer 的默认端口 `9786`。自定义端口、HTTPS 入口和旧版控制台可手动添加。未安装 Tailscale 时仍可使用局域网发现和手动地址。
- **多个地址**会按实例合并。同等探测状态下优先局域网。探测结果反映当前服务器的连通性，浏览器打不开时可返回并展开「访问地址」，改用 Tailscale 或其他入口。手动地址保存在当前服务器，可随时编辑或删除。
- `GET /api/identity` 公开服务标识和随机实例 ID，设备列表和管理接口需要登录。将状态目录复制给另一台设备作为新实例时，移除副本中的 `instance-id.json`，首次访问会生成新 ID。

### 终端和 Codex

- 新建或恢复会话时，点击「浏览目录」可从主目录或根目录逐级选择主机上的目录，也可以显示隐藏目录。
- 终端由 Zellij 承载。浏览器标签关闭或刷新不会终止服务器上的进程；用「结束」明确停止。
- 「运行中的会话」可附着到原会话。关闭标签、刷新页面和网络重连都会保留会话。「恢复历史」另建 Zellij 会话并调用 `codex resume`。
- 「结束」会请求 Zellij 退出指定会话。会话卡住时，在同一个对话框里可以选择「强制结束」，其中运行的进程会被中止。
- 新会话默认使用 Zellij 的 Locked 模式；需要 Zellij 快捷键时按 `Ctrl+g`。
- 新建会话支持拖选后复制到浏览器所在设备。若网页写入剪贴板被限制，点击工具栏「复制选择」。旧会话或 TUI 捕获鼠标时，Windows/Linux 按住 `Shift`、Mac 按住 `Option` 再拖选。手机底部工具栏提供 Esc、Tab、方向键和常用 Ctrl 键。
- 单文件上传上限 25 MiB，保存在 `~/.config/server-console/uploads/`。

### 代码编辑器

在需要编辑代码的主机安装 [code-server](https://coder.com/docs/code-server/install)。macOS 可以运行 `brew install code-server`，Linux 可使用官方安装器或下载安装包。将 `code-server` 放入控制台服务的 PATH（例如 `~/.local/bin`），也可用 `SERVER_CONSOLE_CODE_SERVER=/绝对路径/code-server` 安装控制台常驻服务。控制台首次打开目录时会启动编辑器，无需另启 code-server 服务。

登录后进入「代码编辑器」，点击「浏览目录」选择主机上的目录，再点「打开编辑器」。文件由部署控制台的系统用户直接读写。编辑器占用整个浏览器页面，返回控制台可用浏览器后退。

远程电脑和平板建议通过控制台的 HTTPS 域名访问。已有反向代理时，确保 `/editor/` 的 HTTP 请求和 WebSocket 都转发到控制台。iPad 可在 Safari 中打开，也可把编辑器添加到主屏幕。[code-server 的 iPad 使用说明](https://coder.com/docs/code-server/ipad)

### 我的应用

- 扫描本机监听的 TCP 端口，只列出返回 HTML 的 HTTP 服务；每 30 秒刷新，也可手动扫描。
- 对外监听的服务显示「可直连」，用实际监听地址打开。
- 控制台通过内网 IP 访问时，回环服务会临时开端口并原样转发，10 分钟无活动后关闭；临时端口沿用服务自身的登录机制。
- 控制台通过 SSH 隧道访问时，回环服务会显示一条独立的 `ssh -N -L` 转发命令。
- Server Console 自身和由它启动的 DSH 不会重复列出。

### DeepSeek Harness

打开页面会自动启动 `dsh web`，通过已登录的 `/dsh/` 代理转发 HTTP 和实时消息。DSH 需要支持 `--public-url` 和 `--trusted-host` 的版本；工具默认从服务 PATH 查找，在 macOS 上也识别 `/Applications/DeepSeek Harness.app` 随附的 CLI。

### Mihomo

打开「网络代理」可安装、更新、启动、停止和重启 Mihomo，导入多个 Clash / Mihomo YAML 或订阅，编辑、导出、更新和切换配置；在「策略与节点」切换节点和测速，「连接」「路由规则」「日志」用于排查。每台设备独立保存配置。

Linux 独立后台可以在控制台离线时继续运行；无需启动网页时也可直接使用：

```bash
./console mihomo status|start|stop|restart|rules|logs
```

从 Mac 上已有的 Open Remote Hub 代理迁移时，由管理员执行：

```bash
sudo /usr/bin/python3 -I "$PWD/scripts/migrate_mihomo_macos.py"
```

### 常驻服务

```bash
./console status
./console logs
./console stop
./console start
./console restart
```

- macOS：`~/Library/LaunchAgents/local.server-console.plist`，登录后启动。
- Linux：`~/.config/systemd/user/server-console.service`，可配合 linger 在退出登录后保持。
- 停止控制台或断开浏览器只结束附着客户端，Zellij 会保留现有工作；DSH 随控制台停止，但历史保留在用户目录。

---

## 安全建议

- **控制台密码等于部署账户的 Shell 权限**。用普通账户部署，不要用 root；如果必须用 root，请设置强密码并限制访问网络。
- **默认只监听回环地址**，通过 SSH 隧道访问。需要直连时，只绑定可信内网或 Tailscale 地址，不要用 `0.0.0.0` 暴露到公网。
- **明文 HTTP 下浏览器 Clipboard API 不可用**，终端工具栏会退回兼容模式。需要完整剪贴板/全屏能力时，用 HTTPS 反向代理。
- **我的应用**的临时转发没有额外登录校验，仅在同一网络内可用，闲置 10 分钟关闭；不要在不可信网络开启。
- 上传文件由部署账户可读，定期清理 `~/.config/server-console/uploads/`。

---

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `install` 报无法连接 systemd 用户服务 | 直接 SSH 登录目标账户后重试；或先 `./console run` |
| 退出 SSH 后网页打不开 | 执行 `sudo loginctl enable-linger "$USER"`；没有 sudo 时只能保持登录或用 tmux |
| 本机 9786 被占用 | `./console install --port 19786`，SSH 隧道改成 `-L 19786:127.0.0.1:19786` |
| `git clone` 失败 | 本机打包 tar 或 `git bundle` 后 `scp` 上传；见「获取代码」 |
| 缺少 `curl` / `tar` / `gzip` | 用系统包管理器安装；`bootstrap` 不会自动装系统包 |
| 订阅下载失败或 HTTP 429 | 服务商限流或网络受限；换网络/等限流解除，或从能访问订阅的设备下载 YAML 后粘贴导入 |
| 导入配置提示「Mihomo 校验超时」 | Linux 独立服务和网页启动的内核会预取检测到的 Geo 数据。检查 `geox-url`、`proxy-providers` 和 `rule-providers` 的实际可达性。macOS 系统 TUN 的 Geo 下载由独立内核负责 |
| Codex 登录失败 | 用 `codex login --device-auth`；需要代理时先设置 `HTTPS_PROXY` / `HTTP_PROXY` |
| DSH 登录弹窗一闪而过 | 用 API Key，或在「登录与配置」准备账号登录并通过单独的 SSH 隧道访问 DSH 原生端口 |
| `./console logs` 没有输出 | 确认 `systemctl --user` 可用；root 部署且无独立 user journal 时可用 `journalctl _SYSTEMD_USER_UNIT=server-console.service -n 80 -f` |
| 终端复制不了 | 旧会话可用 Shift（Mac 用 Option）拖选，再点工具栏「复制选择」。若浏览器限制写入，按弹窗提示手动复制 |

---

## 项目结构

```text
console              # 命令行入口：bootstrap/password/install/run/logs...
server/              # FastAPI 后端（host/terminal/docker/ssh/apps/mihomo/dsh）
frontend/            # React + Vite 前端
scripts/             # bootstrap.sh、Mihomo daemon、macOS 迁移脚本
tests/               # 单元测试
```

---

## 开发

```bash
./console setup
./console password
./console run --reload

# 另开一个终端
npm run dev --prefix frontend
```

打开 Vite 打印的本地地址。开发代理会转发 API、终端 WebSocket 和 DSH 请求。发布前执行 `npm run build --prefix frontend`，后端会直接提供 `frontend/dist/`。后端使用单个 worker，以保持认证状态和 DSH 进程在同一个服务实例中。

运行订阅解析测试：

```bash
.venv/bin/python -m unittest tests.test_subscriptions
```

---

## 上游文档

- [Zellij CLI](https://zellij.dev/documentation/commands)
- [DeepSeek Harness CLI 参考](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md)
- [Codex CLI](https://developers.openai.com/codex/cli/)
- [Codex 认证](https://developers.openai.com/codex/auth)
- [Mihomo](https://github.com/MetaCubeX/mihomo)
- [MetaCubeX Geo 数据](https://github.com/MetaCubeX/meta-rules-dat/releases/tag/latest)
- [Docker rootless](https://docs.docker.com/engine/security/rootless/)
