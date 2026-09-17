#!/bin/sh
# OrzMC 一键安装器(macOS / Linux)—— 严格 POSIX sh。
#
# 用法(主推,一条命令):
#   curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh
# 指定版本(绕过 GitHub API 限流):
#   curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh -s -- --version v2.0.1
# 其它选项:--dir <path> / --no-modify-rc / --file <path>(本地安装,测试接缝)/ --uninstall。
#
# 约束:macOS /bin/sh 是 bash 3.2 的 POSIX 模式,Linux /bin/sh 是 dash——
#   无数组、无 [[ ]]、无 &>、无 sed -i、无 jq;JSON 解析用 grep/sed。

set -eu

REPO="OrzMC/OrzPythonMC"
BASE_URL="https://github.com/${REPO}"
PAGES_URL="https://orzmc.github.io/OrzPythonMC/install.sh"

# 参数与运行期状态(set -u 下必须全部初始化)
VERSION=""
DIR=""
NO_RC=""
FILE=""
MODE="install"
URL=""
ASSET=""
PLATFORM=""
INSTALL_DIR=""
BINARY=""
RC_FILE=""
PATH_FILE=""
PATH_LINE=""
STATE_DIR=""
MANIFEST=""
ROOT_DIR=""
RECORD_VERSION=""

die() {
    echo "错误: $*" >&2
    exit 1
}

show_help() {
    cat <<'EOF'
OrzMC 一键安装器(macOS / Linux)

用法:
  curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh
  curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh -s -- --version v2.0.1

选项:
  --version vX.Y.Z   安装指定版本(绕过 GitHub API 限流)
  --dir <path>       自定义安装目录(默认 ${XDG_BIN_HOME:-$HOME/.local/bin})
  --no-modify-rc     不修改 shell 配置文件(仅打印手动 export 提示)
  --file <path>      从本地文件安装(测试 / 开发用)
  --uninstall        反向卸载:读 manifest 删二进制 + 还原 PATH(不删游戏数据;
                     常规卸载请用内置的 orzmc self-uninstall)
  --help             显示本帮助

卸载(内置,推荐):
  orzmc self-uninstall [--yes] [--remove-root] [--force]

升级(内置,也可重跑本命令覆盖安装):
  orzmc update [--check] [-v vX.Y.Z]
EOF
}

# ── 参数解析 ────────────────────────────────────────────────────────────────
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help | -h)
            show_help
            exit 0
            ;;
        --uninstall) MODE="uninstall" ;;
        --no-modify-rc) NO_RC=1 ;;
        --version)
            [ "$#" -ge 2 ] || die "--version 需要一个参数(如 --version v2.0.1)"
            VERSION="$2"
            shift
            ;;
        --version=*) VERSION="${1#*=}" ;;
        --dir)
            [ "$#" -ge 2 ] || die "--dir 需要一个参数"
            DIR="$2"
            shift
            ;;
        --dir=*) DIR="${1#*=}" ;;
        --file)
            [ "$#" -ge 2 ] || die "--file 需要一个参数"
            FILE="$2"
            shift
            ;;
        --file=*) FILE="${1#*=}" ;;
        *)
            die "未知参数: $1(用 --help 查看用法)"
            ;;
    esac
    shift
done

# ── 平台探测 ────────────────────────────────────────────────────────────────
detect_platform() {
    case "$(uname -s)" in
        Darwin)
            case "$(uname -m)" in
                x86_64) ASSET="orzmc-macos-x86_64" && PLATFORM="macos-x86_64" ;;
                arm64) ASSET="orzmc-macos-arm64" && PLATFORM="macos-arm64" ;;
                *) die "暂不支持该 macOS 架构: $(uname -m)" ;;
            esac
            ;;
        Linux)
            case "$(uname -m)" in
                x86_64 | amd64) ASSET="orzmc-linux-x86_64" && PLATFORM="linux-x86_64" ;;
                aarch64 | arm64) ASSET="orzmc-linux-arm64" && PLATFORM="linux-arm64" ;;
                *) die "暂不支持该 Linux 架构: $(uname -m)" ;;
            esac
            ;;
        *) die "暂不支持该系统: $(uname -s)(本安装器仅支持 macOS / Linux)" ;;
    esac
}

# ── 下载地址解析 ─────────────────────────────────────────────────────────────
# 最新版解析的兜底:releases/latest 会 302 到 releases/tag/<tag>。这个端点不是 REST
# API,不受「60 次/时/IP」限流影响(CI runner 与共享 NAT 用户常被限流)。
latest_tag_from_redirect() {
    # 注意:不能带 -L(跟随完重定向后 %{redirect_url} 会是空串)。
    REDIRECT="$(curl -fsS -o /dev/null -w '%{redirect_url}' "${BASE_URL}/releases/latest" 2>/dev/null || true)"
    case "$REDIRECT" in
        */releases/tag/*) printf '%s\n' "${REDIRECT##*/releases/tag/}" ;;
        *) : ;;
    esac
}

resolve_url() {
    if [ -n "$FILE" ]; then
        return # 本地文件安装,无 URL
    fi
    if [ -n "$VERSION" ]; then
        case "$VERSION" in
            v*) : ;;
            *) VERSION="v${VERSION}" ;;
        esac
        URL="${BASE_URL}/releases/download/${VERSION}/${ASSET}"
        return
    fi
    # ORZMC_API_LATEST 是测试/镜像接缝:指到不可用地址即可验证限流兜底。
    API="${ORZMC_API_LATEST:-https://api.github.com/repos/${REPO}/releases/latest}"
    BODY="$(curl -fsSL --retry 3 "$API" 2>/dev/null || true)"
    case "$BODY" in
        *'"API rate limit exceeded"'*) BODY="" ;;
    esac
    if [ -n "$BODY" ]; then
        URL="$(printf '%s\n' "$BODY" | grep -o '"browser_download_url": *"[^"]*'"${ASSET}"'[^"]*"' | head -n1 | sed 's/.*": *"//; s/"$//')"
        if [ -n "$URL" ]; then
            return
        fi
        die "最新 release 中找不到资产 ${ASSET}(可能尚未发布该平台产物)。请指定版本: --version vX.Y.Z"
    fi
    # API 限流/网络失败 → 退回 302 端点,自己拼资产地址。
    LATEST_TAG="$(latest_tag_from_redirect)"
    if [ -z "$LATEST_TAG" ]; then
        die "无法获取最新版本信息(网络问题或 GitHub API 限流)。请指定版本重试: sh -s -- --version vX.Y.Z"
    fi
    VERSION="$LATEST_TAG"
    URL="${BASE_URL}/releases/download/${LATEST_TAG}/${ASSET}"
}

# ── 下载与校验 ───────────────────────────────────────────────────────────────
verify_magic() {
    # 仅校验远程下载产物;--file(本地构建)跳过。
    if [ -n "$FILE" ]; then
        return
    fi
    MAGIC="$(od -An -tx1 -N4 "$1" | tr -d ' \n')"
    case "$MAGIC" in
        cffaedfe | cefaedfe) : ;; # Mach-O 64(小端 / 字节交换)
        cafebabe) : ;;            # 通用二进制(fat)
        7f454c46) : ;;            # ELF
        *)
            rm -f "$1"
            die "下载产物校验失败(非可执行文件,可能命中错误页/代理页)。已中止安装。"
            ;;
    esac
}

download() {
    TMP="$(mktemp "${TMPDIR:-/tmp}/orzmc.XXXXXX")"
    if [ -n "$FILE" ]; then
        cp "$FILE" "$TMP"
    else
        if ! curl -fL --retry 3 -o "$TMP" "$URL" 2>/dev/null; then
            rm -f "$TMP"
            die "下载失败: $URL"
        fi
    fi
    if [ ! -s "$TMP" ]; then
        rm -f "$TMP"
        die "下载产物为空,已中止安装"
    fi
    verify_magic "$TMP"
    chmod +x "$TMP"
}

# ── 安装 ─────────────────────────────────────────────────────────────────────
install_binary() {
    mkdir -p "$INSTALL_DIR"
    mv -f "$TMP" "$BINARY"
}

# ── PATH 登记(幂等) ─────────────────────────────────────────────────────────
pick_rc() {
    case "${SHELL:-}" in
        *zsh) RC_FILE="$HOME/.zshrc" ;;
        *bash) RC_FILE="$HOME/.bashrc" ;;
        *) RC_FILE="$HOME/.profile" ;;
    esac
}

register_path() {
    PATH_FILE=""
    PATH_LINE=""
    if [ -n "$NO_RC" ] || [ -n "${ORZMC_NO_RC:-}" ]; then
        return
    fi
    # 运行期 PATH 已含 install_dir → 无需修改
    case ":$PATH:" in
        *":$INSTALL_DIR:"*) return ;;
    esac
    pick_rc
    [ -f "$RC_FILE" ] || : > "$RC_FILE" # 不存在则创建,保证后续可写
    PATH_LINE="export PATH=\"$INSTALL_DIR:\$PATH\""
    if grep -Fqx "$PATH_LINE" "$RC_FILE" 2>/dev/null; then
        return # 行已存在(幂等)
    fi
    # 保证 rc 文件以换行结尾再追加,避免把行拼到上一行末尾
    if [ -s "$RC_FILE" ] && [ -n "$(tail -c1 "$RC_FILE")" ]; then
        printf '\n' >> "$RC_FILE"
    fi
    printf '%s\n' "$PATH_LINE" >> "$RC_FILE"
    PATH_FILE="$RC_FILE"
}

# ── manifest 写入 ────────────────────────────────────────────────────────────
write_manifest() {
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/orzmc"
    mkdir -p "$STATE_DIR"
    MANIFEST="$STATE_DIR/install.conf"
    if [ -n "$FILE" ]; then
        RECORD_VERSION="local-build"
    else
        # 从 URL 提取 tag(如 v2.0.1),保证 version 字段有值
        RECORD_VERSION="$(printf '%s\n' "$URL" | sed -n 's#.*/releases/download/\([^/]*\)/.*#\1#p')"
        [ -n "$RECORD_VERSION" ] || RECORD_VERSION="$VERSION"
    fi
    ROOT_DIR="${ORZMC_ROOT_DIR:-$HOME/minecraft}"
    printf 'tool=orzmc\nschema=1\nversion=%s\nplatform=%s\ninstall_dir=%s\nbinary=%s\n' \
        "$RECORD_VERSION" "$PLATFORM" "$INSTALL_DIR" "$BINARY" > "$MANIFEST"
    if [ -n "$URL" ]; then
        printf 'source=%s\n' "$URL" >> "$MANIFEST"
    fi
    if [ -n "$PATH_FILE" ]; then
        printf 'path_file=%s\n' "$PATH_FILE" >> "$MANIFEST"
    fi
    if [ -n "$PATH_LINE" ]; then
        printf 'path_line=%s\n' "$PATH_LINE" >> "$MANIFEST"
    fi
    printf 'root_dir=%s\n' "$ROOT_DIR" >> "$MANIFEST"
}

# ── 安装主流程 ───────────────────────────────────────────────────────────────
do_install() {
    detect_platform
    if [ -n "${ORZMC_BIN:-}" ]; then
        INSTALL_DIR="$ORZMC_BIN"
    elif [ -n "$DIR" ]; then
        INSTALL_DIR="$DIR"
    else
        INSTALL_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
    fi
    BINARY="$INSTALL_DIR/orzmc"
    resolve_url
    download
    install_binary
    register_path
    write_manifest

    echo "[OK] orzmc 已安装到 $BINARY"
    if [ -n "$PATH_FILE" ]; then
        echo "已写入 PATH 配置: $PATH_FILE"
        echo "请重新打开终端,或执行: source $PATH_FILE"
    elif [ -n "$PATH_LINE" ]; then
        : # 无 rc 修改(运行期 PATH 已含 install_dir),无需提示
    else
        echo "未修改 shell 配置;若当前 PATH 不含 $INSTALL_DIR,请手动执行:"
        echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
    fi
    echo "升级:orzmc update(或重新执行本命令覆盖安装)。"
    echo "卸载: orzmc self-uninstall --yes(游戏数据默认保留;--remove-root 连 ~/minecraft 一起删)"
}

# ── 卸载(shell 兜底;常规卸载请用 orzmc self-uninstall)──────────────────────
do_uninstall() {
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/orzmc"
    MANIFEST="$STATE_DIR/install.conf"
    if [ ! -f "$MANIFEST" ]; then
        echo "未找到安装记录($MANIFEST)。若已安装,请运行 orzmc self-uninstall 或手动删除二进制。"
        exit 0
    fi
    BIN="$(sed -n 's/^binary=//p' "$MANIFEST" | head -n1)"
    INSTALL_DIR="$(sed -n 's/^install_dir=//p' "$MANIFEST" | head -n1)"
    PATH_FILE="$(sed -n 's/^path_file=//p' "$MANIFEST" | head -n1)"
    PATH_LINE="$(sed -n 's/^path_line=//p' "$MANIFEST" | head -n1)"
    if [ -n "$BIN" ] && [ -f "$BIN" ]; then
        rm -f "$BIN"
        echo "已删除二进制: $BIN"
    fi
    if [ -n "$PATH_FILE" ] && [ -n "$PATH_LINE" ] && [ -f "$PATH_FILE" ]; then
        if grep -Fqx "$PATH_LINE" "$PATH_FILE"; then
            # 精确行删除;cp 保留原文件权限,in 处替换不改变 inode 与权限。
            cp "$PATH_FILE" "${PATH_FILE}.orzmc.bak"
            grep -Fvx "$PATH_LINE" "${PATH_FILE}.orzmc.bak" > "$PATH_FILE"
            rm -f "${PATH_FILE}.orzmc.bak"
            echo "已还原 PATH 配置: $PATH_FILE"
        else
            echo "未在 $PATH_FILE 中找到记录的 PATH 行,跳过还原"
        fi
    fi
    rm -f "$MANIFEST"
    rmdir "$STATE_DIR" 2>/dev/null || true
    [ -n "$INSTALL_DIR" ] && rmdir "$INSTALL_DIR" 2>/dev/null || true
    echo "卸载完成(游戏数据已保留)。"
}

# ── 入口 ─────────────────────────────────────────────────────────────────────
if [ "$MODE" = "uninstall" ]; then
    do_uninstall
else
    do_install
fi
