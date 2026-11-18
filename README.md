# Server Console

面向个人 Mac 和 Linux 主机的浏览器工作台。一个 FastAPI 用户服务同时提供 React 前端、API 和 WebSocket。数据保存在当前用户目录，运行时使用现有的 Zellij、Docker、OpenSSH、Tailscale、Codex 和 DeepSeek Harness。

## 从一台刚拿到的 Ubuntu 服务器开始

假设你有服务器的内网 IP、Linux 用户名和 SSH 密码。下例用 `student@192.168.1.50`，请换成课题组实际分配的用户和地址。用自己的普通 Linux 账户部署，控制台中的终端和 AI 工具会沿用该账户及其项目权限。

**在自己的电脑打开 PowerShell 或终端，连接服务器。**

```bash
ssh -L 9786:127.0.0.1:9786 student@192.168.1.50
```

**在这次 SSH 登录里运行。**

```bash
git clone https://github.com/AaronChou313/server-console.git
cd server-console
./console bootstrap
./console password
./console install
```

保持 SSH 窗口连接，在自己电脑的浏览器中打开 [http://127.0.0.1:9786](http://127.0.0.1:9786)，输入刚设置的控制台密码。它与 SSH 密码独立，支持 4–256 个字符，包括四位数字。也可运行 `./console generate-password`，首次生成的密码保存在仅当前用户可读的 `~/.config/server-console/initial-password`。改密码后执行 `./console restart` 使已有网页登录失效。

- `bootstrap` 在 Ubuntu 22.04 及更高版本和 macOS 的 amd64 / arm64 上按需准备 Python 3.11+、Node.js 24 与 Zellij 0.45+，然后安装项目依赖并构建前端。下载使用 GitHub、Node.js、PyPI 和 npm 等上游，软件安装在当前用户目录。Ubuntu 缺少系统工具时，请有权限的人运行 `sudo apt install git curl ca-certificates`。
- 出现 systemd 用户服务连接失败时，先用 `./console run` 前台运行。常驻部署应直接 SSH 登录目标账户，使系统创建用户服务环境。有管理员权限时可运行 `sudo loginctl enable-linger "$USER"`，让用户服务在退出 SSH 后保留，并在重启后启动。
- 如果本机 9786 端口已被占用，将 SSH 命令中的第一个 `9786` 改为 `19786`，浏览器打开 [http://127.0.0.1:19786](http://127.0.0.1:19786)。
- 手动准备好 Python 3.11+、Node.js 20.19+ 或 22.12+ 和 Zellij 0.45+ 的机器，可用 `./console setup` 构建。macOS 可使用相同步骤，常驻服务在部署账户登录 macOS 后启动。

### 直连内网 IP

若机房允许访问服务器的 9786/TCP，可以在服务器上执行 `./console install --host 192.168.1.50`，浏览器打开该 IP 的 9786 端口。登录后可使用当前服务器账户的 Shell。共用网段建议沿用默认的 SSH 转发，避免经明文 HTTP 传送登录和终端数据。若希望访问内网地址但浏览器仍提供增强剪贴板和全屏功能，可使用自己的 HTTPS 反向代理，传递原始 Host、可信转发协议及 WebSocket Upgrade。

不同客户端网段可以共用同一个服务器 IP，只要校园网或机房路由允许这些网段访问该 IP 与端口。如果浏览器到 9786 不通但 SSH 可达，继续使用 SSH 转发。必须经跳板机登录时，在同一条命令里使用 `-J` 指定跳板机。

```bash
ssh -J student@bastion.lab.example -L 9786:127.0.0.1:9786 student@192.168.1.50
```

服务器有多个独立网卡时，选客户端能到达的服务端接口 IP。其他网段仍可走原有 SSH 路径。如果两个网段之间既无路由也无 SSH / 跳板路径，需要由机房管理员提供访问路径。

Tailscale 完全可选，默认安装流程无需组网。如果已获准使用并已完成组网，可执行 `./console install --host "$(tailscale ip -4)"`，从 Tailnet 地址访问。

### 添加网络代理和 AI 工具

基础控制台可先部署，工具可按需补装。Ubuntu 想一次准备完整工具时，将首次安装命令替换为 `./console bootstrap --with-ai --with-mihomo`。在支持 systemd 用户服务的 Linux 上，这会同时安装独立 Mihomo 用户服务。macOS 的自动安装只下载内核，已有系统代理的迁移见下文。已有可执行文件和账号配置会继续使用。

**Mihomo**

1. 若只想安装代理，运行 `./console install-mihomo`。Linux 再运行 `./console install-mihomo-service` 启用独立后台，它可以在控制台离线时保持运行。
2. 打开「网络代理 → 配置 → 导入配置」，粘贴服务商的 Clash / Mihomo 订阅 URL。也可导入文件或粘贴 YAML。标准或 Base64 包装的 Clash 配置、常见纯节点订阅均可识别。
3. 点「使用配置」后启动 Mihomo。在「策略与节点」先测速再选出站节点。Ubuntu 的代理默认监听 `127.0.0.1:17890`，按需在要用代理的命令中设置 `HTTPS_PROXY` 和 `HTTP_PROXY`。
4. 若订阅下载失败，可从能访问订阅的设备下载 Clash YAML，再上传到服务器。后续在配置编辑页补上订阅链接，可直接更新。若报 HTTP 429，等服务商限流解除再试。如果配置引用 GEOIP、GEOSITE 或远程规则，内核首次校验也需要访问这些数据源。下载受限时，请使用可达的 `geox-url` 和规则源，或从官方数据发行页预置配置引用的 `country.mmdb`、`geoip.metadb`、`geoip.dat`、`geosite.dat`。Linux 独立服务的数据目录是 `~/.config/server-console/mihomo-service/`，网页直接启动的内核使用 `~/.config/server-console/mihomo/`。[官方 Geo 数据](https://github.com/MetaCubeX/meta-rules-dat/releases/tag/latest)

**Codex 和 DeepSeek Harness**

```bash
./console bootstrap --with-ai
./console restart
export PATH="$HOME/.local/share/server-console/node24/bin:$HOME/.local/bin:$PATH"
codex login status
```

- Codex 需能访问账号认证和模型端点。远程主机上可在 SSH 中运行 `codex login --device-auth`，在自己电脑的浏览器完成官方登录。若账号未允许设备码认证，可按官方说明启用，或通过标准输入运行 `codex login --with-api-key`。登录后，在「AI 工作台 → Codex」选项目目录并创建会话。[Codex 认证](https://developers.openai.com/codex/auth)
- 若本机 Mihomo 路由可达 OpenAI，可在 Web 终端中显式通过代理执行 Codex 登录与交互，例如 `HTTPS_PROXY=http://127.0.0.1:17890 HTTP_PROXY=http://127.0.0.1:17890 codex login --device-auth`。启动交互时也需要相同的代理环境。要让「AI 工作台 → Codex」的新会话继承该环境，见下方「为工具指定代理」。
- DSH 走「AI 工作台 → DeepSeek Harness」提供的官方 Web 界面。首次使用时在自己的 DSH 设置中配置 DeepSeek 提供方和 API Key。若账号登录采用回环回调，远程浏览器打不开回调页面时，优先使用官方 Web 中的 API Key 方式。DSH 和 Codex 都使用当前服务器账户自己的凭据及会话。[DSH 官方 CLI 参考](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md)
- Docker 可选。控制台沿用部署账户已能访问的 Docker Engine。先用 `docker ps` 测试访问权，再使用 Docker 页面。实验室主机请沿用已部署的 Engine，或向管理员申请符合机房政策的访问方式。[Docker rootless 指南](https://docs.docker.com/engine/security/rootless/)

### 为工具指定代理

若希望由工作台新拉起的工具进程使用本机 Mihomo，请在 SSH 中设置以下变量，再用原来的监听参数重新执行 `install`。以下是默认回环监听，若之前使用了 `--host` 或 `--port`，请在此追加它们。

```bash
SERVER_CONSOLE_TOOL_PROXY=http://127.0.0.1:17890 ./console install
```

该设置供工作台新建的 Codex 和 DSH 进程使用，其他已有终端会话保持原来的环境。使用独立 Mihomo 服务时，可先在「网络代理」确认代理端口和运行状态。

## 日常操作

| 模块 | 操作 |
| --- | --- |
| 概览 | 查看 CPU、内存、用户磁盘卷、网络速率与运行时间。每 3 秒采样，趋势保留在当前浏览器内存中。 |
| 我的应用 | 自动发现本机监听并返回 HTML 的 Web 服务；点击直接打开，回环地址服务经控制台临时转发。 |
| 终端 | 新建项目 Shell，或在工具栏展开「会话」连接已有的 Zellij 会话。浏览器标签关闭、刷新和 WebSocket 重连都会保留服务器进程。用「结束」明确停止。 |
| Docker | 查看 Engine 状态、容器、CPU 和内存，启动、停止、重启、查看末尾日志或进入容器。容器中需要可用的 `/bin/bash` 或 `/bin/sh`。 |
| SSH | 系统 OpenSSH 发起连接。主配置的独立 `Host` 条目可编辑，`Include` 或多别名配置可以查看和连接。保存前在 `~/.ssh/config.server-console.bak` 留一份备份。认证使用现有密钥和系统代理。 |
| 网络 | 查看网络接口、当前用户权限下可读取的连接、Tailscale Peer 和链路信息。支持 Ping、TCP、Tailscale Ping 和 Netcheck。 |
| 网络代理 | 管理本机 Mihomo 内核、配置和订阅，切换策略组节点、测试延迟，查看规则、连接和日志。 |
| AI 工作台 | 在终端使用原生 Codex CLI，在 DeepSeek Harness 页面使用官方 Web 界面。 |
| 设置 | 在当前浏览器保存亮暗主题、终端字体与字号。 |

### 终端和 Codex

- 从终端工具栏打开「运行中的会话」并点击「连接」，即可附着到原来的 Zellij 会话。内部状态、当前审批提示和 Shell 环境由原进程持有。主机重启会终止这些进程。
- 「恢复历史」新建一个 Zellij 容器会话，再使用官方 `codex resume`。提供项目内历史选择、所有项目历史、最近会话及 UUID。它读取当前服务器用户的 Codex 历史。
- 新建的 Zellij 会话默认使用 Locked 模式，尽量把按键留给内部程序。需要 Zellij 本身的快捷键时，按 `Ctrl+g` 切换。点击全屏或隐藏侧栏可以增大终端面积。
- 连接主机上已有的 Zellij 会话后，如果进入了滚动模式，可按 `Esc` 或点击底部的「恢复输入」。
- 在桌面用浏览器的粘贴、`Ctrl+Shift+C` 或终端工具栏复制、粘贴。手机上点终端呼出键盘，底部工具栏提供 Esc、Tab、方向键和常用 Ctrl 按键。不同浏览器和 TUI 的选区行为有差异，鼠标模式下可尝试按住 Shift 拖选。
- 用上传按钮发送附件，或在终端里直接粘贴浏览器能够读到的图片。上传完成后可以复制或插入服务器路径。单文件限 25 MiB，文件保存在 `~/.config/server-console/uploads/`，由该用户自行清理。

### 我的应用

- 扫描主机上监听的 TCP 端口，只列出返回 HTML 的 HTTP 服务。页面每 30 秒自动刷新，也可以手动「重新扫描」。
- 对外监听的服务标记为「可直连」，通过该服务实际监听的地址打开。
- 控制台通过内网 IP 访问时，可为主机本机应用在该接口上临时开端口，10 分钟无活动后关闭。临时端口沿用应用自身的登录机制。控制台通过 SSH 隧道访问时，可点击「主机本机」应用查看独立的 SSH 转发命令。
- Server Console 自身和由控制台启动的 DSH Web 不会重复列出。

### DeepSeek Harness

打开 DeepSeek Harness 页面会自动启动 `dsh web`。Server Console 在主机回环地址启动官方进程，并通过已登录的 `/dsh/` 代理转发 HTTP 和实时消息。使用「新标签页打开」可在独立浏览器标签访问同一个官方页面。DSH 需要支持 `--public-url` 和 `--trusted-host` 的版本。

工具默认从服务的 PATH 查找。在 macOS 上也识别 `/Applications/DeepSeek Harness.app` 随附的 DSH CLI。前台运行可用 `SERVER_CONSOLE_DSH` 等同名环境变量指定可执行文件路径。

### Mihomo

打开「网络代理」可安装、更新、启动、停止和重启 Mihomo。也支持服务 PATH 中的 [官方 Mihomo 可执行文件](https://github.com/MetaCubeX/mihomo/releases)，可用 `SERVER_CONSOLE_MIHOMO` 指定路径。内核下载安装需要服务器能够访问 GitHub。

在「配置」页可导入多个 Clash / Mihomo YAML 文件或订阅链接，编辑、导出、更新和切换配置。导入会先由 Mihomo 校验，可以在任意配置上点击「使用配置」。在「策略与节点」可切换规则、全局和直连模式，选择节点、恢复自动组，并执行单节点、组内或批量测速。「连接」页支持断开单条或全部连接，「路由规则」和「日志」可用于排查。每台设备独立保存配置，文件位于 Server Console 状态目录下的 `mihomo/`。

独立后台通过私有 Unix socket 接受管理命令，Server Console 网页服务停止时后台继续运行。

- Linux 可在部署账户下执行 `./console install-mihomo-service`，安装并启用 `server-console-mihomo.service`。独立内核默认提供本机 `127.0.0.1:17890` 的 HTTP / SOCKS5 代理。
- 从 Mac 上已有的 Open Remote Hub 代理迁移时，由当前管理员执行 `sudo /usr/bin/python3 -I "$PWD/scripts/migrate_mihomo_macos.py"`。该命令复制现有配置，保留原代理端口和 TUN 状态，并切换到独立的 `local.server-console.mihomo` 系统服务。新服务校验失败时会尝试恢复旧服务。
- 无需启动网页，可以在部署账户下运行 `./console mihomo status`、`start`、`stop`、`restart`、`rules` 或 `logs`。

## 常驻服务

```bash
./console status
./console logs
./console stop
./console start
./console restart
```

macOS 使用 `~/Library/LaunchAgents/local.server-console.plist`，在用户登录后启动。Linux 使用 `~/.config/systemd/user/server-console.service`。若 Linux 主机应在用户登出后保持服务，启用该用户的 systemd linger。服务停止和浏览器断开只结束控制台的附着客户端，Zellij 保留现有工作。Server Console 重启后重新登录即可接回已有的 Zellij 会话。DSH Web 服务随 Server Console 停止，DSH 自身历史存放在原来的用户目录。

## 更新

在原仓库和同一账户下运行 `git pull --ff-only && ./console setup && ./console restart`。控制台数据保留在 `~/.config/server-console/`。使用前台运行时，退出旧进程后重新执行 `./console run`。

## 开发

```bash
./console setup
./console password
./console run --reload
# 另开一个终端
npm run dev --prefix frontend
```

打开 Vite 打印的本地地址。开发代理会转发 API、终端 WebSocket 和 DSH 请求。发布前运行 `npm run build --prefix frontend`，后端会直接提供 `frontend/dist/`。后端使用单个 worker，以便认证状态与 DSH 进程在同一个服务实例中维护。

## 上游文档

- [Zellij CLI](https://zellij.dev/documentation/commands)
- [DeepSeek Harness CLI 参考](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md)
- [Codex CLI](https://developers.openai.com/codex/cli/)
