#!/usr/bin/env bash
set -euo pipefail

die() { printf 'sp2 安装失败：%s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法：./install.sh [--prefix DIR] [--skip-deps]

支持 macOS 和 Ubuntu；自动安装缺失的 Python 3.8+ 和 Supervisor。
  --prefix DIR   安装到 DIR/bin/sp2（默认：$HOME/.local）
  --skip-deps    只检查依赖，不调用包管理器
  -h, --help    显示帮助

示例：
  ./install.sh
  ./install.sh --prefix /usr/local
EOF
}

prefix="${HOME}/.local"
skip_deps=false
while (($#)); do
    case "$1" in
        --prefix)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die '--prefix 需要目录参数'
            prefix="$2"
            shift 2
            ;;
        --skip-deps) skip_deps=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（使用 --help 查看用法）" ;;
    esac
done
[[ "$prefix" == /* ]] || die '--prefix 必须是绝对路径'

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$source_dir/sp2" ]] || die '请保留 install.sh 与 sp2 在同一目录'

case "$(uname -s)" in
    Darwin) platform=macos ;;
    Linux)
        [[ -r /etc/os-release ]] || die '无法识别 Linux 发行版，仅支持 Ubuntu'
        # os-release is supplied by the operating system.
        . /etc/os-release
        [[ "${ID:-}" == ubuntu ]] || die 'Linux 目前仅支持 Ubuntu'
        platform=ubuntu
        ;;
    *) die '仅支持 macOS 和 Ubuntu' ;;
esac

as_root() {
    if [[ "$(id -u)" == 0 ]]; then
        "$@"
    else
        command -v sudo >/dev/null 2>&1 || die '需要管理员权限，但找不到 sudo'
        sudo "$@"
    fi
}

python_ready() {
    command -v python3 >/dev/null 2>&1 &&
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1
}

supervisor_ready() {
    command -v supervisorctl >/dev/null 2>&1 && command -v supervisord >/dev/null 2>&1
}

# Homebrew may be installed but not yet available in this shell's PATH.
if [[ "$platform" == macos ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        for brew_bin in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [[ -x "$brew_bin" ]]; then
                export PATH="$(dirname -- "$brew_bin"):$PATH"
                break
            fi
        done
    fi
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix)"
        export PATH="$brew_prefix/bin:$PATH"
    fi
fi

packages=()
if ! python_ready; then
    if [[ "$platform" == macos ]]; then packages+=(python); else packages+=(python3); fi
fi
supervisor_ready || packages+=(supervisor)
if ((${#packages[@]})); then
    "$skip_deps" && die '缺少 Python 3.8+ 或 Supervisor；移除 --skip-deps 可自动安装'
    if [[ "$platform" == macos ]]; then
        command -v brew >/dev/null 2>&1 || die '请先安装 Homebrew：https://brew.sh，然后重新运行脚本'
        brew install "${packages[@]}"
    else
        as_root apt-get update
        as_root apt-get install -y "${packages[@]}"
    fi
    hash -r
fi
python_ready || die '需要 Python 3.8+；请升级 Python 后重新运行'
supervisor_ready || die '未找到 supervisorctl 和 supervisord；请检查安装结果及 PATH'

bin_dir="${prefix%/}/bin"
[[ ! -d "$bin_dir/sp2" ]] || die "$bin_dir/sp2 已存在且指向目录"

# Stage beside the destination, then rename so an existing symlink is replaced
# without writing through it (it may point back to the repository).
stage=''
ancestor="$bin_dir"
while [[ ! -e "$ancestor" ]]; do ancestor="$(dirname -- "$ancestor")"; done
elevate=false
[[ -d "$ancestor" && -w "$ancestor" ]] || elevate=true
install_run() {
    if "$elevate"; then as_root "$@"; else "$@"; fi
}
cleanup() { if [[ -n "$stage" ]]; then install_run rm -f -- "$stage"; fi; }
trap cleanup EXIT
install_run mkdir -p -- "$bin_dir"
stage="$(install_run mktemp "$bin_dir/.sp2.XXXXXX")"
install_run install -m 755 "$source_dir/sp2" "$stage"
install_run mv -f -- "$stage" "$bin_dir/sp2"
stage=''

printf '已安装：%s\n' "$bin_dir/sp2"
"$bin_dir/sp2" --help >/dev/null
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) printf '请将以下配置加入 shell 配置文件（如 ~/.zshrc 或 ~/.bashrc），并重新打开终端：\nexport PATH=%q:"$PATH"\n' "$bin_dir" ;;
esac
if [[ "$platform" == macos && -n "${brew_prefix:-}" ]]; then
    printf 'Homebrew Supervisor 配置示例：\nexport SP2_CONFIG=%q\n' "$brew_prefix/etc/supervisord.conf"
    printf '如需启动 Supervisor：brew services start supervisor\n'
elif [[ "$platform" == ubuntu ]]; then
    printf 'Supervisor 配置示例：\nexport SP2_CONFIG=/etc/supervisor/supervisord.conf\n'
    printf '连接示例：sp2 --sudo ls\n如需启动 Supervisor：sudo systemctl start supervisor\n'
fi
