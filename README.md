# OrzMC 2.0

[![官网](https://img.shields.io/badge/官网-orzmc.github.io/OrzPythonMC-15803d)](https://orzmc.github.io/OrzPythonMC/)
[![PyPI](https://img.shields.io/pypi/v/orzmc-app?label=orzmc-app)](https://pypi.org/project/orzmc-app/)
[![License](https://img.shields.io/github/license/OrzMC/OrzPythonMC)](LICENSE)

跨平台 Minecraft **客户端启动 / 服务端部署**工具,一条命令即可安装与启动。多版本并存、Java 运行时自动托管(不依赖系统 Java),发布为 PyPI 包与各平台独立二进制。

> **两个包的分工(M3)**:
> - **`orzmc-app`** —— 命令行程序(`orzmc`)。**产品主路径**
> - **`orzmc`**(本仓库同名的库) —— **供 Python 程序调用的能力库**(服务端部署 / 客户端启动 / 备份 / 插件 …),
>   供脚本与自动化使用。它**不是**产品主路径,新功能优先落在 Swift 实现,**不再承诺 API 稳定扩张**
>   (破坏性变更会走 minor + CHANGELOG 说明)。

### 运行时通道（`ORZMC_RUNTIME`）

`orzmc` 命令的**功能实现**是 [Swift 版](https://github.com/OrzMC/OrzMCKit);PyPI 包负责装/升级它并转发。

| 情况 | 默认行为 |
|---|---|
| **新装**（`pip install orzmc-app`，没有安装记录） | 默认走 **Swift 真身**（首次运行自动下载 13–35 MB，SHA256 校验） |
| **一键安装器装的**（`install.sh` / `install.ps1`，含**新装**；有 `install.conf`） | 默认仍走 **Python 实现**（安装器目前装的就是 PyInstaller 二进制，再下 Swift 真身等于白下两遍；见下） |
| `ORZMC_RUNTIME=swift` | 强制走 Swift 真身 |
| `ORZMC_RUNTIME=python` | 强制走 Python 实现（**逃生阀**，永久有效） |

```sh
orzmc runtime status     # 看当前用哪套、真身版本、是否复用了已有原生安装
orzmc runtime install    # 预下载真身（已有原生 Swift 安装时是 no-op）
```

> **Python 引擎的维护窗口**：自 `2.17.0` 起 **6 个月**内（至 2027-04-20），Python 实现只修
> **安全 / 数据损坏 / 无法启动** 三类问题，不再开发新功能；到期后归档，运行时通道的「存量安装」
> 豁免也会一并移除。想提前用上单一实现：`export ORZMC_RUNTIME=swift`。
>
> **下一步（已排期）**：把一键安装器改成直接安装 Swift 二进制（而不是 PyInstaller 二进制），
> 之后新装一律是单一实现，并可下线 PyInstaller 的 6 平台资产（省一半发布矩阵）。

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

安装器自动识别平台、下载最新二进制、登记 PATH、**装 Shell 补全**并写入安装记录;重复执行即覆盖升级。更多选项见 `install.sh --help`(`--version vX.Y.Z` 固定版本、`--dir` 自定义目录、`--no-completion` 不装补全、`--no-modify-rc` 不动 shell 配置等)。已装好后也可用内置的 `orzmc update` 自升级(见下文)。

**方式二:其它方式**

- **PyPI**(需 Python 3.10+):`pip install orzmc-app`
- **独立二进制**:从 [Releases](https://github.com/OrzMC/OrzPythonMC/releases) 下载对应平台的二进制(`orzmc-macos-*` / `orzmc-linux-*` / `orzmc-windows-*`),解压后直接运行。官网页面会自动识别你的平台,给出对应下载链接。
- **uv 用户**:`uv tool install orzmc-app`(卸载用 `uv tool uninstall orzmc-app`)

## 发布与可信来源

- **二进制**:每个平台的文件都是由 `release.yml` 在 GitHub Actions 上从对应 tag 现场构建的,构建后先自检 `orzmc version` 与 tag 一致(防止分支代码混进产物),再挂到该 Release。
- **PyPI 包**:`orzmc` / `orzmc-app` 通过 **OIDC Trusted Publisher** 发布(仓库里不存在任何长期 API token),并且每个发行文件都附带 **PEP 740 产源证明(provenance)** —— 一份由 sigstore 签名的构建声明,用来证明「这个 wheel 确实由本仓库的那次 Actions 运行构建」。

```bash
# 1) 快速判断某个文件有没有产源证明(HTTP 200 = 有,404 = 无)
curl -sI https://pypi.org/integrity/orzmc/2.4.0/orzmc-2.4.0-py3-none-any.whl/provenance

# 2) 完整校验:连签名、构建仓库一起验(把版本号换成你要查的)
uvx "pypi-attestations==0.0.30" verify pypi "pypi:orzmc-2.4.0-py3-none-any.whl" \
  --repository https://github.com/OrzMC/OrzPythonMC
# → OK: orzmc-2.4.0-py3-none-any.whl
```

`--repository` 填的是**构建产地仓库**(签名声明里的来源),不是发布者 —— 它必须与 provenance 里的值一致,否则校验会明确报 `provenance was signed by repository "X", expected "Y"`,这正是该校验的意义。`orzmc_app` 的同理(文件名换成 `orzmc_app-<版本>-py3-none-any.whl`)。

Minecraft 游戏文件(Mojang / Adoptium / Fabric / Forge)的完整性则按各自官方的 SHA-1 校验:下载器在写入前比对官方清单,不匹配即拒绝(见下文「核心特性」)。

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
orzmc uninstall [--yes] [--remove-root] [--force]
```

- 删除 orzmc 二进制、还原安装器登记的 PATH 修改、清理空目录。
- 游戏数据(默认 `~/minecraft`)默认保留;`--remove-root` 连游戏数据一起删。
- `--yes` 免确认;**非交互(脚本 / CI)调用必须带 `--yes`**,否则按规范返回取消并退出 130。
- 与 `orzmc remove`(移除某个 **Minecraft 版本**)不同,`uninstall` 卸载的是**工具本身**。

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

# Shell 补全(bash/zsh/fish):按 $SHELL 落到标准补全目录,幂等;卸载时一并清理
orzmc completion install
orzmc completion zsh > ~/.zsh/completions/_orzmc   # 也可以只打印脚本自己装

# 升级 orzmc 自身(GitHub 最新发布;--check 只查询,--version 指定版本)
orzmc update --check
orzmc update
```

## 命令行

每个子命令都可加 `--help`(或 `-h`)查看完整参数(如 `orzmc client --help`)。

```
orzmc [--verbose] [--json] [--root-dir DIR]   # 无子命令 → 打印帮助(--json 时返回 E_USAGE 信封)
orzmc client   [-v VER] [--username|-u USER] [-t vanilla|fabric|forge] [-m MIN] [-x MAX]
               [--auth] [--extract-music] [--jvm-opts ...] [--refresh] [-j THREADS] [--root-dir DIR]
orzmc server   [-v VER] [-t vanilla|paper|fabric|forge] [-m MIN] [-x MAX]
               [--force-upgrade] [--symlink] [--force-download] [--yes]
               [--jvm-opts ...] [--server-args ...] [--nogui] [--demo] [--print-jar-help]
               [--online-mode] [--kill-all] [--refresh] [-j THREADS] [--root-dir DIR]
orzmc remove   -v VER [--server -t TYPE] [--yes]
orzmc update   [-v VER] [--check] [--channel release|beta] [--file PATH] [--yes] [--force]
orzmc uninstall [--yes] [--remove-root] [--force]          # 非交互需 --yes
orzmc list
orzmc backup   [-v VER] [-t TYPE]
orzmc plugin   [-s 关键词] [-l] [-o 目录] [-v 游戏版本] [-c 数量]   # Hangar 搜索 / 下载服务端插件
orzmc app      install|open|uninstall|status                # macOS 应用
orzmc version | capabilities | mcp
orzmc completion bash|zsh|fish|powershell|pwsh      # 打印补全脚本到 stdout
orzmc completion install                          # 按 $SHELL 装到标准补全目录(幂等;不改执行策略;powershell 不支持自动安装)
```

要点:

- **版本缺省**:有 TTY 时弹出键盘导航选择器(`↑↓` 选择、`←→` / PgUp / PgDn 翻页、输入即过滤、`t` 切正式 / 测试版、`x` 清空、Enter 选中、Esc 用最新);脚本 / 管道等非 TTY 场景自动用最新 release 与默认值,不阻塞。
- **玩家名**:`client` 默认 `guest`;TTY 下未指定 `-u/--username` 会交互询问(回车用默认);脚本 / 管道等非 TTY 场景静默用默认。
- **正版登录**:`client --auth` 走 **Microsoft 设备码**流程,令牌存本机状态目录 `auth.json`(权限 `0600`),只用于本机启动。已内置公共 `client_id`,**开箱即用**;需要时可用环境变量 `ORZMC_AZURE_CLIENT_ID` 覆盖(详见父仓库 `docs/spec/auth.md`)。
- **运行即安装**:`client` / `server` 检测到文件缺失会自动下载补齐;下载进度显示**大小 / 速度 / 剩余时间**(如 `35.6/39.2 MB 6.4 MB/s 0:00:01`),批量资源阶段显示文件计数与百分比,元数据请求也有不定长进度条 —— 慢网不会静默等待。
- **下载并发**:`-j/--download-threads`(1-64,默认 16)。资源阶段是几千个几十 KB 的小文件,耗时几乎完全由**每请求延迟 乘 并发数**决定而不是带宽:在延迟高 / 走代理的链路上把它调大(如 `-j 32`)能成倍提速(实测 8→32 线程约 2.2 倍);连接池会跟着并发自动放大,不会因池太小而反复重建 TLS;`-j 1` 表示完全单流。
- **大文件分块**:客户端 jar / 服务端 jar / JRE(≥8MB)自动分 4 路 `Range` 并行下载再拼接(实测 39MB 客户端 jar 单流 2.5 MB/s → 4 路 4.0 MB/s),服务器忽略 `Range`(部分代理/镜像)时自动回退单流,拼接后仍按 SHA-1 校验。
- **元数据缓存**:版本清单、Fabric 元数据、Paper 构建、Forge promotions 统一缓存 **24 小时**,期间直接复用缓存(离线也能启动已装版本);加 `--refresh` 强制重新拉取。
- **自升级**:`orzmc update` 升级工具自身二进制(`--check` 只查询,`-v/--version` 指定版本或绕过 GitHub API 限流,`--file` 离线/本地安装)。为保证正在运行的程序不被弄坏,替换由分离助手在命令退出后完成——**下一次运行才是新版本**;pip/pipx 托管安装会被拒绝并提示用 `pip install -U orzmc-app`。
- **Shell 补全**:`orzmc completion install` 按 `$SHELL`(Windows 视为 powershell)把脚本装到标准目录并**幂等**覆盖——bash 落在 `~/.local/share/bash-completion/completions/orzmc`(必要时补一行 `source`)、zsh 落在 `~/.zsh/completions/_orzmc`(必要时补 `fpath`)、fish 落在 `~/.config/fish/completions/orzmc.fish`;`powershell`/`pwsh` 只生成脚本、不改执行策略、不自动落盘。落点会登记进安装记录,**`orzmc uninstall` 会一并删除**。
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

# CLI 行为验收(真实进程:帮助/信封/退出码/取消语义/补全/MCP;按规范断言)
uv run python scripts/accept_cli.py
uv run python scripts/accept_cli.py --cli dist/orzmc      # 同一套断言跑冻结二进制(发布前必跑)

# 真实下载 Minecraft / Java 并启动
uv run --package orzmc-app python scripts/acceptance.py \
    --case server:vanilla:latest --case client:vanilla:latest \
    --root /tmp/orzmc-accept
```

## 行为变化(2.12 起)

- `--install-completion` / `--show-completion` **已移除**(与 `completion` 子命令重复):改用
  `orzmc completion install`(自动装)或 `orzmc completion <shell>`(只打印脚本)。
- **非交互**(脚本 / 管道 / CI)下,`remove` / `uninstall` 必须显式 `--yes`,否则按规范返回取消并退出 **130**
  (以前是「静默不删」或「静默删掉」两种实现各一套)。
- 用法错误(未知命令 / 未知选项 / 缺必填)统一退出 **1**(框架默认的 2 已归一)。
- 错误信息一律写 **stderr**;`--json` 下 stdout **只有**信封。
- `--json` 且不带子命令 → `E_USAGE` 信封 + 退出 1(不再往 stdout 打帮助)。

## 兼容性说明(v1 → v2)

- 旧顶层 `orzmc -s -v 26.2` 改为 `orzmc server -v 26.2`;客户端 / 服务端统一 `-v/-t/-m/-x`。
- 旧 `-E "a:..."` 改为 `--jvm-opts "..."` 与 `--server-args "..."`。
- 目录结构改为统一布局;`orzmc list` 可查看已装版本与类型,便于迁移。
- 移除 nginx / rsync / daemon 等系统级能力;直连官方源。

---

<!-- ORZMC:COMPLIANCE:BEGIN (auto-generated by scripts/sync_compliance.py; edit docs/compliance/) -->
> **NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.**
>
> OrzMC 是独立的社区开源项目，与 Mojang / Microsoft 无隶属、赞助或背书关系，对本项目负责。
>
> “Minecraft”名称、品牌与素材的一切权利归 Mojang / Microsoft 所有。
>
> 联系：[GitHub 组织](https://github.com/OrzMC) ｜ 隐私政策：https://orzmc.jokerhub.cn/privacy/
<!-- ORZMC:COMPLIANCE:END -->
