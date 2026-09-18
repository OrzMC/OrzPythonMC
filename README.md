# OrzMC 2.0

[![官网](https://img.shields.io/badge/官网-orzmc.github.io/OrzPythonMC-15803d)](https://orzmc.github.io/OrzPythonMC/)
[![PyPI](https://img.shields.io/pypi/v/orzmc-app?label=orzmc-app)](https://pypi.org/project/orzmc-app/)
[![License](https://img.shields.io/github/license/OrzMC/OrzPythonMC)](LICENSE)

跨平台 Minecraft **客户端启动 / 服务端部署**工具,一条命令即可安装与启动。多版本并存、Java 运行时自动托管(不依赖系统 Java),发布为 PyPI 包与各平台独立二进制。

## 核心特性

- **多版本并存**:客户端 / 服务端一版本一目录,互不干扰
- **Java 自动托管**:按版本下载 JRE 到应用目录,无需系统 Java
- **即装即用**:文件缺失自动补齐,无需独立 install 步骤
- **服务端一键部署**:自动接受 EULA、写配置;`--nogui` 无窗口启动,`stop` / Ctrl-C 优雅关闭
- **交互式版本选择器**:键盘导航 TUI,输入即过滤,响应式适配终端尺寸
- **跨平台**:macOS / Linux / Windows × x86_64 / arm64,发布即用

## 安装

**方式一:一条命令(推荐,无需 Python / pip)**

macOS / Linux:

```bash
curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh
```

Windows(PowerShell 5.1+):

```powershell
irm https://orzmc.github.io/OrzPythonMC/install.ps1 | iex
```

安装器自动识别平台、下载最新二进制、登记 PATH 并写入安装记录;重复执行即覆盖升级。更多选项见 `install.sh --help`(`--version vX.Y.Z` 固定版本、`--dir` 自定义目录等)。已装好后也可用内置的 `orzmc update` 自升级(见下文)。

**方式二:其它方式**

- **PyPI**(需 Python 3.10+):`pip install orzmc-app`
- **独立二进制**:从 [Releases](https://github.com/OrzMC/OrzPythonMC/releases) 下载对应平台的二进制(`orzmc-macos-*` / `orzmc-linux-*` / `orzmc-windows-*`),解压后直接运行。官网页面会自动识别你的平台,给出对应下载链接。
- **uv 用户**:`uv tool install orzmc-app`(卸载用 `uv tool uninstall orzmc-app`)

## 升级

```bash
orzmc update             # 升级到 GitHub 最新发布
orzmc update --check     # 只检查是否有新版本
orzmc update -v v2.1.0   # 指定版本(也是 GitHub API 限流时的回退)
orzmc update --file ./orzmc -v v2.1.0   # 离线用本地二进制升级
```

- 为保证正在运行的程序不被弄坏,二进制替换由**分离助手在命令退出后**完成——提示「本命令退出后生效」,**下一次运行才是新版本**。
- 无参数查询走 GitHub Releases API 的未认证额度(60 次/时/IP);设有 `GITHUB_TOKEN` / `GH_TOKEN` 时自动带上(5000 次/时),限流或离线时用 `-v vX.Y.Z` 指定版本即可完全绕过 API。
- pip / pipx 安装会被拒绝并提示用 `pip install -U orzmc-app` / `pipx upgrade orzmc-app`。
- 直接重跑一键安装器同样会覆盖升级,效果相同。

## 卸载

**内置统一卸载(推荐)**

```bash
orzmc self-uninstall [--yes] [--remove-root] [--force]
```

- 删除 orzmc 二进制、还原安装器登记的 PATH 修改、清理空目录。
- 游戏数据(默认 `~/minecraft`)默认保留;`--remove-root` 连游戏数据一起删;`--yes` 免确认(脚本化调用)。
- 与 `orzmc remove`(移除某个 **Minecraft 版本**)不同,`self-uninstall` 卸载的是**工具本身**。

**按安装方式手动卸载(备用)**

- pip 安装:`pip uninstall orzmc-app`
- 独立二进制:删除对应可执行文件(如 `~/.local/bin/orzmc` 或 `orzmc.exe`)
- 游戏数据同样默认保留,需要时手动清理(如 `rm -rf ~/minecraft`)。

## 快速开始

```bash
# 每个子命令都支持 --help 查看参数(如 orzmc client --help 可看到 -u/--username 等)
orzmc client --help

# 启动最新版(26.2)原版客户端;-v 缺省时自动用 Mojang 最新 release
# 玩家名默认 guest;TTY 下未指定会交互询问,回车用默认;也可 -u/--username 显式指定
orzmc client -v 26.2 -u Steve
orzmc client -v 26.2            # 交互输入玩家名(回车用默认 guest)

# 以 Fabric / Forge 启动客户端(自动装 loader / 官方安装器收割)
orzmc client -v 26.2 -t fabric -u Steve
orzmc client -v 26.2 -t forge -u Steve

# 部署并启动 Paper / Fabric 服务端(自动接受 EULA)
orzmc server -v 26.2 -t paper --yes --nogui
orzmc server -v 26.2 -t fabric --yes

# 管理已安装版本
orzmc list
orzmc remove -v 26.2 --yes

# 升级 orzmc 自身(GitHub 最新发布;--check 只查询,--version 指定版本)
orzmc update --check
orzmc update
```

## 命令行

每个子命令都可加 `--help` 查看完整参数(如 `orzmc client --help`)。

```
orzmc [--verbose]                 # 无子命令 → 打印帮助
orzmc client   [-v VER] [--username|-u USER] [-t vanilla|fabric|forge] [-m MIN] [-x MAX]
               [--extract-music] [--jvm-opts ...] [--refresh] [-j THREADS]
orzmc server   [-v VER] [-t vanilla|paper|fabric|forge] [-m MIN] [-x MAX]
               [--force-upgrade] [--symlink] [--force-download] [--yes]
               [--jvm-opts ...] [--server-args ...] [--nogui] [--refresh] [-j THREADS]
orzmc remove   -v VER [--server -t TYPE] [--yes]
orzmc update   [-v VER] [--check] [--file PATH] [--yes] [--force]
orzmc self-uninstall [--yes] [--remove-root] [--force]
orzmc list
orzmc backup   [-v VER] [-t TYPE]
orzmc version
```

要点:

- **版本缺省**:有 TTY 时弹出键盘导航选择器(`↑↓` 选择、`←→` / PgUp / PgDn 翻页、输入即过滤、`t` 切正式 / 测试版、`x` 清空、Enter 选中、Esc 用最新);脚本 / 管道等非 TTY 场景自动用最新 release 与默认值,不阻塞。
- **玩家名**:`client` 默认 `guest`;TTY 下未指定 `-u/--username` 会交互询问(回车用默认);脚本 / 管道等非 TTY 场景静默用默认。
- **运行即安装**:`client` / `server` 检测到文件缺失会自动下载补齐;下载进度显示**大小 / 速度 / 剩余时间**(如 `35.6/39.2 MB 6.4 MB/s 0:00:01`),批量资源阶段显示文件计数与百分比,元数据请求也有不定长进度条 —— 慢网不会静默等待。
- **下载并发**:`-j/--download-threads`(1-64,默认 16)。资源阶段是几千个几十 KB 的小文件,耗时几乎完全由**每请求延迟 乘 并发数**决定而不是带宽:在延迟高 / 走代理的链路上把它调大(如 `-j 32`)能成倍提速(实测 8→32 线程约 2.2 倍);连接池会跟着并发自动放大,不会因池太小而反复重建 TLS;`-j 1` 表示完全单流。
- **大文件分块**:客户端 jar / 服务端 jar / JRE(≥8MB)自动分 4 路 `Range` 并行下载再拼接(实测 39MB 客户端 jar 单流 2.5 MB/s → 4 路 4.0 MB/s),服务器忽略 `Range`(部分代理/镜像)时自动回退单流,拼接后仍按 SHA-1 校验。
- **元数据缓存**:版本清单、Fabric 元数据、Paper 构建、Forge promotions 统一缓存 **24 小时**,期间直接复用缓存(离线也能启动已装版本);加 `--refresh` 强制重新拉取。
- **自升级**:`orzmc update` 升级工具自身二进制(`--check` 只查询,`-v/--version` 指定版本或绕过 GitHub API 限流,`--file` 离线/本地安装)。为保证正在运行的程序不被弄坏,替换由分离助手在命令退出后完成——**下一次运行才是新版本**;pip/pipx 托管安装会被拒绝并提示用 `pip install -U orzmc-app`。
- **服务端关闭**:终端输入 `stop` 保存退出,或按 **Ctrl-C**(等待保存退出,超 60s 才强制结束,不留孤儿进程)。
- **类型**:客户端 `vanilla|fabric|forge`;服务端 `vanilla|paper|fabric|forge`。
- **Java**:版本要求读自版本 JSON,自动下载 Temurin JRE 到 `java/<大版本>/`,无需完整 JDK。

## 目录结构(统一,多版本并存)

```
<root>/                                  # 默认 ~/minecraft,可用 --root-dir 指定
  versions/<mc_version>/                 # 一个版本一目录,客户端/服务端并存
    client/   assets/  libraries/  natives/  <version>.jar  ...
    server/<type>/   <core>.jar  eula.txt  server.properties  world/  ...
  java/<java_major>/                     # 应用托管的 JRE/JDK,不依赖系统 java
  cache/  backup/worlds/  backup/music/<ver>/
```

## 开发

项目为 **uv workspace**(库 `orzmc` + 应用 `orzmc-app`),架构约定见 [AGENTS.md](AGENTS.md)。

```bash
uv sync --all-packages        # 安装依赖(锁定 3.12 工具链)
uv run --all-packages pytest  # 全部测试(库 + 应用)
uv run ruff check .           # lint
uv run --all-packages mypy    # 类型检查
uv build --all-packages       # 构建两个包
```

## 自动化验收与 CI

跨平台由四个 GitHub Actions 工作流保证(**6 平台** = macOS / Linux / Windows × x86_64 / arm64):

- **`ci.yml`**(push / PR):质量门禁 + 6 平台 pytest + 合并后 6 平台二进制构建与安装器 e2e。
  想在**合并前**跑重活(二进制 + 安装器),用正规入口,不必改工作流:
  `gh workflow run ci.yml --ref <分支> -f full=true`
- **`acceptance.yml`**(每日 + 手动):真实下载 Minecraft / Java 并启动,以最新版为主基准,`backcompat` 冒烟旧版本;失败会自动开 issue
- **`release-please.yml`**(push main):按 Conventional Commits 自动开 release PR(版本号 + `CHANGELOG.md`),合并即发版
- **`release.yml`**(打 `v*` 标签):先校验 tag 与代码版本一致,再 6 平台二进制挂 Release、`orzmc` / `orzmc-app` 双包发布 PyPI,最后才让 Release 可见

发版与协作细则见 [`CONTRIBUTING.md`](CONTRIBUTING.md),架构红线见 [`AGENTS.md`](AGENTS.md)。

本地跑真实验收:

```bash
# 安装器 + 自升级闭环(隔离临时目录,不碰 PATH / rc / ~/minecraft)
# Windows 需两个 PowerShell 各跑一遍(5.1 是默认 shell,7.x 行为并不相同)
uv run python scripts/accept_selfupdate.py
uv run python scripts/accept_selfupdate.py --skip-build --powershell pwsh --expect-ps-major 7

# 真实下载 Minecraft / Java 并启动
uv run --package orzmc-app python scripts/acceptance.py \
    --case server:vanilla:latest --case client:vanilla:latest \
    --root /tmp/orzmc-accept
```

## 兼容性说明(v1 → v2)

- 旧顶层 `orzmc -s -v 26.2` 改为 `orzmc server -v 26.2`;客户端 / 服务端统一 `-v/-t/-m/-x`。
- 旧 `-E "a:..."` 改为 `--jvm-opts "..."` 与 `--server-args "..."`。
- 目录结构改为统一布局;`orzmc list` 可查看已装版本与类型,便于迁移。
- 移除 nginx / rsync / daemon 等系统级能力;直连官方源。
