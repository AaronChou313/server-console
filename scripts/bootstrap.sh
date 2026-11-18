#!/usr/bin/env bash
set -euo pipefail

ai=false
proxy=false
for option in "$@"; do
  case "$option" in
    --with-ai) ai=true ;;
    --with-mihomo) proxy=true ;;
    *) echo "用法：./console bootstrap [--with-ai] [--with-mihomo]" >&2; exit 2 ;;
  esac
done

case "$(uname -m)" in
  x86_64|amd64) arch=x86_64; node_arch=x64 ;;
  aarch64|arm64) arch=aarch64; node_arch=arm64 ;;
  *) echo "自动安装支持 amd64 和 arm64。其他架构请手动准备依赖后运行 ./console setup。" >&2; exit 1 ;;
esac
case "$(uname -s)" in
  Darwin) target="$arch-apple-darwin"; node_os=darwin ;;
  Linux) target="$arch-unknown-linux-musl"; node_os=linux ;;
esac

for tool in curl tar gzip; do
  if ! command -v "$tool" >/dev/null; then
    echo "缺少 $tool，请先使用系统包管理器安装。" >&2
    exit 1
  fi
done

bin="$HOME/.local/bin"
node_home="$HOME/.local/share/server-console/node24"
mkdir -p "$bin"
export PATH="$node_home/bin:$bin:$PATH"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

download() {
  curl --fail --silent --show-error --location --retry 2 --connect-timeout 15 --max-time 180 "$1" --output "$2"
}

if ! command -v node >/dev/null || ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 24 || (major === 24 && minor >= 9) ? 0 : 1)' || ! command -v npm >/dev/null; then
  echo "安装 Node.js 24 到当前用户目录…"
  dist=https://nodejs.org/dist/latest-v24.x
  download "$dist/SHASUMS256.txt" "$work/node-files"
  archive="$(awk -v ending="-$node_os-$node_arch.tar.gz" 'substr($2,length($2)-length(ending)+1)==ending {print $2; exit}' "$work/node-files")"
  test -n "$archive"
  download "$dist/$archive" "$work/node.tar.gz"
  mkdir -p "$node_home"
  tar -xzf "$work/node.tar.gz" -C "$node_home" --strip-components=1
fi

zellij_version="$(zellij --version 2>/dev/null | awk '{print $2}' || true)"
if ! awk -v version="$zellij_version" 'BEGIN {split(version,v,"."); exit !(v[1]>0 || v[2]>=45)}'; then
  echo "安装 Zellij 0.45.1 到当前用户目录…"
  download "https://github.com/zellij-org/zellij/releases/download/v0.45.1/zellij-no-web-$target.tar.gz" "$work/zellij.tar.gz"
  tar -xzf "$work/zellij.tar.gz" -C "$work" zellij
  install -m 0755 "$work/zellij" "$bin/zellij"
fi

if ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  if ! python3 -c 'import sys, ensurepip; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    if ! command -v uv >/dev/null; then
      echo "安装 uv 到当前用户目录…"
      download https://astral.sh/uv/install.sh "$work/uv-install.sh"
      UV_UNMANAGED_INSTALL="$bin" sh "$work/uv-install.sh"
    fi
    uv venv --python 3.12 --seed .venv
  fi
fi

./console setup
if "$ai"; then
  packages=()
  command -v codex >/dev/null || packages+=(@openai/codex)
  if ! .venv/bin/python -c 'from server.runtime import binary; raise SystemExit(not binary("dsh"))'; then
    packages+=(@deepseek-ai/dsh)
  fi
  if [ "${#packages[@]}" -gt 0 ]; then
    npm install --global --prefix "$HOME/.local" "${packages[@]}"
  fi
fi
if "$proxy"; then
  ./console install-mihomo
  if [ "$node_os" = linux ]; then
    ./console install-mihomo-service
  fi
fi
printf '\n准备完成。首次安装请执行 ./console password && ./console install\n'
