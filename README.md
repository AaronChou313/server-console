# Server Console

面向个人 Mac 和 Linux 主机的浏览器工作台。一个 FastAPI 用户服务同时提供 React 前端、API 和 WebSocket。数据保存在当前用户目录，运行时使用现有的 Zellij、Docker、OpenSSH、Tailscale、Codex 和 DeepSeek Harness。

## 安装与启动

准备 Python 3.11+、Node.js 20.19+ 或 22.12+，以及支持后台会话的 Zellij 0.45+。Docker、Tailscale、Codex 和 DSH 可分别安装。用日常工作的系统账户运行服务，服务继承该账户的文件权限与工具配置。

```bash
./console setup
./console password
./console run
```

前台默认监听 [127.0.0.1:9786](http://127.0.0.1:9786)。配置和上传目录默认位于 `~/.config/server-console/`。也可以使用 `./console generate-password`，首次密码会写入权限为 `0600` 的 `initial-password` 文件。可用 `./console password` 设置 4–256 个字符的密码，支持四位数字。更改后执行 `./console restart` 可让已有网页登录失效。

## 通过 Tailscale 使用

在主机和访问设备上登录 Tailscale，把服务绑定到主机当前的 Tailnet IPv4。

```bash
./console install --host "$(tailscale ip -4)" --port 9786
```

在 Windows、平板或手机的浏览器打开安装命令打印的地址。该命令绑定指定的 Tailscale 接口。无参数的 `./console install` 只绑定回环地址。登录账户拥有当前服务器用户的交互式 Shell，应将监听接口和 Tailnet 访问规则设置在可信设备范围内。

HTTP 可以使用浏览器自带的粘贴事件与文件选择器。需要浏览器 Clipboard API、全屏等增强能力时，可将服务绑定回环地址，由自己的 HTTPS 反向代理提供访问。保留原始 Host、可信的转发协议和 WebSocket Upgrade。反向代理与 Server Console 同机时，默认信任来自回环代理的转发协议。Tailscale IP 直连时不需要再部署代理。

## 日常操作

| 模块 | 操作 |
| --- | --- |
| 概览 | 查看 CPU、内存、用户磁盘卷、网络速率与运行时间。每 3 秒采样，趋势保留在当前浏览器内存中。 |
| 终端 | 新建项目 Shell，或在工具栏展开「会话」连接已有的 Zellij 会话。浏览器标签关闭、刷新和 WebSocket 重连都会保留服务器进程。用「结束」明确停止。 |
| Docker | 查看 Engine 状态、容器、CPU 和内存，启动、停止、重启、查看末尾日志或进入容器。容器中需要可用的 `/bin/bash` 或 `/bin/sh`。 |
| SSH | 系统 OpenSSH 发起连接。主配置的独立 `Host` 条目可编辑，`Include` 或多别名配置可以查看和连接。保存前在 `~/.ssh/config.server-console.bak` 留一份备份。认证使用现有密钥和系统代理。 |
| 网络 | 查看网络接口、当前用户权限下可读取的连接、Tailscale Peer 和链路信息。支持 Ping、TCP、Tailscale Ping 和 Netcheck。 |
| 网络代理 | 管理本机 Mihomo，导入 Clash / Mihomo 配置，切换代理组中的节点，并检测 OpenAI 和 DeepSeek API 的连通性。 |
| AI 工作台 | 在终端使用原生 Codex CLI，在 DeepSeek Harness 页面使用官方 Web 界面。 |
| 设置 | 在当前浏览器保存亮暗主题、终端字体与字号。 |

### 终端和 Codex

- 从终端工具栏打开「运行中的会话」并点击「连接」，即可附着到原来的 Zellij 会话。内部状态、当前审批提示和 Shell 环境由原进程持有。主机重启会终止这些进程。
- 「恢复历史」新建一个 Zellij 容器会话，再使用官方 `codex resume`。提供项目内历史选择、所有项目历史、最近会话及 UUID。它读取当前服务器用户的 Codex 历史。
- 新建的 Zellij 会话默认使用 Locked 模式，尽量把按键留给内部程序。需要 Zellij 本身的快捷键时，按 `Ctrl+g` 切换。点击全屏或隐藏侧栏可以增大终端面积。
- 连接主机上已有的 Zellij 会话后，如果进入了滚动模式，可按 `Esc` 或点击底部的「恢复输入」。
- 在桌面用浏览器的粘贴、`Ctrl+Shift+C` 或终端工具栏复制、粘贴。手机上点终端呼出键盘，底部工具栏提供 Esc、Tab、方向键和常用 Ctrl 按键。不同浏览器和 TUI 的选区行为有差异，鼠标模式下可尝试按住 Shift 拖选。
- 用上传按钮发送附件，或在终端里直接粘贴浏览器能够读到的图片。上传完成后可以复制或插入服务器路径。单文件限 25 MiB，文件保存在 `~/.config/server-console/uploads/`，由该用户自行清理。

### DeepSeek Harness

打开 DeepSeek Harness 页面会自动启动 `dsh web`。Server Console 在主机回环地址启动官方进程，并通过已登录的 `/dsh/` 代理转发 HTTP 和实时消息。使用「新标签页打开」可在独立浏览器标签访问同一个官方页面。DSH 需要支持 `--public-url` 和 `--trusted-host` 的版本。

工具默认从服务的 PATH 查找。在 macOS 上也识别 `/Applications/DeepSeek Harness.app` 随附的 DSH CLI。前台运行可用 `SERVER_CONSOLE_DSH` 等同名环境变量指定可执行文件路径。

### Mihomo

在目标设备安装 [官方 Mihomo 可执行文件](https://github.com/MetaCubeX/mihomo/releases)，放到服务的 PATH，或通过 `SERVER_CONSOLE_MIHOMO` 指定可执行文件。打开「网络代理」导入标准 Clash / Mihomo YAML（可上传文件或粘贴内容）并启动。每台设备独立保存配置和节点选择，文件位于 Server Console 状态目录下的 `mihomo/`。

Server Console 管理的 Mihomo 接受本机 `127.0.0.1:17890` 的 HTTP 和 SOCKS5 连接，管理接口使用私有 Unix socket。启动 Mihomo 后，需要代理的应用可单独配置该端口。初始配置为直连，导入自己的代理配置后即可选择节点。

## 常驻服务

```bash
./console status
./console logs
./console stop
./console start
./console restart
```

macOS 使用 `~/Library/LaunchAgents/local.server-console.plist`，在用户登录后启动。Linux 使用 `~/.config/systemd/user/server-console.service`。若 Linux 主机应在用户登出后保持服务，启用该用户的 systemd linger。服务停止和浏览器断开只结束控制台的附着客户端，Zellij 保留现有工作。Server Console 重启后重新登录即可接回已有的 Zellij 会话。DSH Web 服务随 Server Console 停止，DSH 自身历史存放在原来的用户目录。

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
