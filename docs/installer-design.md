# OrzMC 通用一键安装 / 卸载方案设计

> 状态:评审稿。定位是**工具自身的安装与卸载**,与游戏版本管理(`orzmc remove`)无关。
> 评审通过后按本文档实现;架构级结论沉淀进 `AGENTS.md`。

## 1. 背景与目标

### 1.1 为什么 pip 在 macOS 不生效

问题不在 orzmc 本身(独立二进制在 macOS 完全正常),而在「用 pip 装 Python 包」这个环节:

1. **macOS 不预装 Python 3**(12.3 起连 Python 2 都没有),`pip` 经常不存在或指向非预期解释器。
2. **Homebrew Python 是 PEP 668 `externally-managed-environment`**,`pip install` 直接拒绝写入系统 site-packages。
3. `pip install --user` 装到 `~/Library/Python/3.x/bin`,**不在 PATH** 上,装完 `orzmc` 还是找不到。

### 1.2 目标

- **一条命令**完成安装 / 升级 / 卸载,在 macOS / Linux / Windows 行为一致。
- **屏蔽跨平台差异**:平台差异收敛到安装器脚本层,用户只看到同一条命令。
- **不依赖 Python / pip**,彻底绕开 PEP 668。
- 卸载统一、可清理(二进制 + PATH 修改 + 可选游戏数据),不遗留痕迹。
- **官网(主页)同时说明安装与卸载**:卸载与安装方式一一对应,并明确游戏数据默认保留、按需清理。

## 2. 现状盘点

| 项 | 现状 |
|---|---|
| 安装方式 | ① `pip install orzmc-app` ② 从 Releases 下载二进制解压即用 ③ 官网提示 uv 用户 `uv tool install orzmc-app` |
| 卸载 | **无统一入口**。pip 方式靠 `pip uninstall`,二进制方式手工删文件,都留下游戏数据 `~/minecraft` |
| 工具自身 install/uninstall 子命令 | **无**。CLI 只有 `orzmc remove`(移除 Minecraft 版本,见 `app.py:168`) |

## 3. 方案总览

采用 **rustup / uv / mise 同款模式**:单行安装器 + 二进制内置自卸载,两层各屏蔽一部分平台差异。

```bash
# macOS / Linux —— 一条命令
curl -fsSL https://orzmc.github.io/OrzPythonMC/install.sh | sh
# Windows (PowerShell) —— 一条命令
irm https://orzmc.github.io/OrzPythonMC/install.ps1 | iex
```

**两层设计:**

```
┌─ 安装器脚本(平台相关,每平台一份)─────────────┐
│  探测 os+arch → 解析最新 release → 下载二进制     │
│  → 放入标准位置 → 登记 PATH → 写安装 manifest      │
└──────────────────────────────────────────────┘
        │ 平台差异全部收敛在这一层(sh / PowerShell)
        ▼
┌─ 二进制内置卸载(平台无关,一份代码)────────────┐
│  orzmc uninstall                            │
│  读 manifest → 还原 PATH → 删自身 → 可选删游戏数据 │
└──────────────────────────────────────────────┘
```

- **安装器**负责「装到哪、PATH 怎么改」——这是唯一的平台差异点,由各平台脚本自己处理。
- **卸载**由二进制自带子命令完成——orzmc 运行期天然知道 `argv[0]` 与 `root_dir`,卸载逻辑与平台无关。

## 4. 组件设计

### 4.1 `install.sh`(阶段一)

**约束**:仅依赖 POSIX `sh`(macOS 上是 bash 3.2 的 POSIX 模式)+ `curl`;不依赖 jq、不依赖 bash 特性(无数组、无 `[[ ]]`、无 `&>`)——这决定了解析 JSON 用 `grep`/`sed` 而非 jq。

**主流程(伪代码):**

```sh
1. 探测平台:
     uname -s  |  uname -m   →   asset
     Darwin     x86_64           orzmc-macos-x86_64
     Darwin     arm64            orzmc-macos-arm64
     Linux      x86_64           orzmc-linux-x86_64
     Linux      aarch64          orzmc-linux-arm64
     Linux      armv7l/其它      → 报错退出「暂不支持该架构」
2. 决定安装目录:
     优先级: $ORZMC_BIN > --dir <路径> > ${XDG_BIN_HOME:-$HOME/.local/bin}
     mkdir -p;binary 名固定为 orzmc
3. 解析下载地址(见 4.1.1)
4. 下载到临时文件 → 健全性校验(见 4.1.2)
5. 写入 $INSTALL_DIR/orzmc,chmod +x
6. PATH 登记(见 4.1.3)
7. 写 manifest(见 4.3)
8. 打印结果 + 「重新打开终端或 source <rc> 生效」
```

**4.1.1 解析最新 release(不依赖 jq)**

- 默认:`curl -fsSL https://api.github.com/repos/OrzMC/OrzPythonMC/releases/latest`,用 `grep -o '"browser_download_url": *"[^"]*orzmc-<os>-<arch>[^"]*"'` 直接捞出包含目标 asset 名的下载地址(asset 名内嵌在 URL 中,无需 jq)。
- **限流兜底**:响应体含 `"API rate limit exceeded"` 时打印友好错误,提示 `--version vX.Y.Z`。
- `--version vX.Y.Z`:跳过 API,直接拼 `https://github.com/OrzMC/OrzPythonMC/releases/download/<tag>/orzmc-<os>-<arch>`。

**4.1.2 下载健全性校验**

- `curl -fL --retry 3 -o "$tmp" "$url"`;要求文件非空。
- 用 `od -An -tx1 -N4` 检查 magic:Mach-O `cf fa ed fe`、ELF `7f 45 4c 46`;命中 HTML(`<!DOCTYPE`)或 JSON(`{`)即判失败——防止 302 落到错误页 / 代理页。
- 校验失败:删除临时文件,报「下载产物异常」,不安装。

**4.1.3 PATH 登记(幂等)**

- 运行时先判断 bin 目录是否已在 `$PATH`(`case ":$PATH:" in *":$bin:"*)`),在则跳过。
- 按 `$SHELL` 选 rc 文件:`*zsh → ~/.zshrc`、`*bash → ~/.bashrc`、其它 `→ ~/.profile`。
- 追加行 `export PATH="<bin>:$PATH"`;写前 `grep -Fqx` 判重,已存在则跳过。
- 记录「改过哪个文件、哪一行」进 manifest(卸载时精确还原)。
- `--no-modify-rc` / 环境变量 `ORZMC_NO_RC=1`:跳过 rc 修改,仅打印手动 `export` 提示。
- 父 shell 环境无法改写,只打印「重新打开终端或 `source <rc>`」。

**4.1.4 参数汇总**

| 参数 | 作用 |
|---|---|
| `--version vX.Y.Z` | 固定版本安装(绕过 API 限流) |
| `--dir <path>` | 自定义安装目录 |
| `--no-modify-rc` | 不写 shell rc(仅打印提示) |
| `--uninstall` | 反向操作:读 manifest 删二进制 + 还原 rc + 删 manifest(**不删游戏数据**,仅兜底,常规卸载走 `orzmc uninstall`) |

幂等与升级:重复执行 = 覆盖二进制 + 更新 manifest 版本号,即「一条命令升级」。

### 4.2 `install.ps1`(阶段二,设计对齐)

- 架构探测:`$env:PROCESSOR_ARCHITECTURE`(`AMD64` / `ARM64`)→ `orzmc-windows-x86_64` / `orzmc-windows-arm64`。
- 版本解析:`Invoke-RestMethod https://api.github.com/.../releases/latest`,从 `assets` 里按 `name` 匹配。
- 安装到 `$env:LOCALAPPDATA\Programs\orzmc\orzmc.exe`。
- PATH:`[Environment]::SetEnvironmentVariable('Path', ..., 'User')`(自动广播,无需注销)。
- manifest 写到 `$env:LOCALAPPDATA\orzmc\install.conf`。
- 执行策略:`irm | iex` 不落盘、不受 ExecutionPolicy 限制,无需 `-ExecutionPolicy Bypass`。

### 4.3 manifest 格式(`install.conf`)

**用 key=value 行格式而非 JSON**:安装器是 shell,写 JSON 需要转义 `"` / `\`,key=value 取「首个 `=` 之后的全部」即可,零转义风险,shell 与 Python 都好读写。

```
tool=orzmc
schema=1
version=2.0.1
platform=macos-arm64
install_dir=/Users/joker/.local/bin
binary=/Users/joker/.local/bin/orzmc
source=https://github.com/OrzMC/OrzPythonMC/releases/download/v2.0.1/orzmc-macos-arm64
path_file=/Users/joker/.zshrc
path_line=export PATH="/Users/joker/.local/bin:$PATH"
root_dir=/Users/joker/minecraft
```

- 路径:Unix 在 `${XDG_STATE_HOME:-$HOME/.local/state}/orzmc/install.conf`,Windows 在 `%LOCALAPPDATA%\orzmc\install.conf`。
- `uninstall` 查找顺序:① state 目录 ② 二进制旁 `.orzmc-manifest`(兼容二进制被移动的情形)。
- `root_dir` 记录安装时默认游戏根 `~/minecraft`(与库内 `DEFAULT_ROOT` 一致),供卸载询问「是否连游戏数据一起删」。

### 4.4 `orzmc uninstall` 子命令

**与 `orzmc remove` 明确区分**:`remove` 删 Minecraft 版本(数据),`uninstall` 删工具本身(二进制 + PATH 修改 + 可选数据)。命名用 `uninstall`,避免与 `remove` 歧义。

**选项**:`--yes`(免确认,脚本化调用)、`--remove-root`(连游戏数据根 `~/minecraft` 一起删)、`--root-dir`(覆盖默认)、`--force`(绕过安全护栏)。

**执行序列:**

1. **定位自身**:`Path(sys.argv[0]).resolve()`。
2. **安全护栏**:若二进制在 `.venv` / `site-packages` / `dist-packages` / PyInstaller 临时目录下(疑似 `uv run` / 开发环境),**拒绝执行**,除非 `--force`——防止开发者误删 venv。
3. **读 manifest**:有 → 用其中的 `path_file` / `path_line` 精确还原(只删记录的那一行,用户自行追加的行不动);无 → 仍按 `argv[0]` 删二进制,并打印「未找到安装记录,已尽力清理」警告。
4. **删二进制**;`install_dir` / state 目录**仅当为空**时 `rmdir`(`~/.local/bin` 是共享目录,绝不整体删除)。
5. **询问游戏数据**(默认不删):`--remove-root` 视为同意;`--yes` 默认保留并提示。
6. 打印汇总:删除的文件、还原的 rc 行、保留的游戏数据路径。

**pip 安装形态的降级**:检测到 manifest 缺失且 `argv[0]` 处于 pip 管理的 bin(旁有 `dist-info` / site-packages)时,不删二进制(会破坏 pip 记账),改打印「请用 `pip uninstall orzmc-app`」,仍可提供 `--remove-root` 做数据清理。

**架构分层(遵循 AGENTS.md 改动流程):**

| 层 | 内容 |
|---|---|
| `orzmc/` 库 | 新增 `orzmc/services/selfinstall.py`:`InstallManifest`(读写/查找)+ `uninstall_self(binary, *, root_dir, remove_root, yes, force, reporter)`;纯 fs 注入、无网络,可单测 |
| 公共 API | `orzmc/__init__.py` 导出 `uninstall_self`(公共 API 即契约) |
| `orzmc-app` | `app.py` 新增 `uninstall` 子命令,只调公共 API,复用 `options.py` 的 `Yes` / `RootDir` 模式 |

## 5. 与现有发布链路集成

| 环节 | 改动 |
|---|---|
| `release.yml` | **零改动**——二进制命名已是 `orzmc-<os>-<arch>`(`orzmc-macos-arm64` 等 6 组合),安装器按此命名匹配即可 |
| `pages.yml` | 把 `install.sh` / `install.ps1` 提交到 `docs/`(触发现有 Pages 自动部署),URL 即 `https://orzmc.github.io/OrzPythonMC/install.sh`。`.sh` 的 MIME 不影响 `curl -fsSL \| sh` |
| 官网 `docs/index.html` | **同时更新安装区与卸载区**。安装区主推单行命令(Unix / Windows 两条),二进制下载降为次级,`pip`/`uv` 移入「其它方式」;卸载区与安装一一对应——统一卸载 `orzmc uninstall` 为主,`pip uninstall` / 删二进制为备,并说明游戏数据默认保留、如何清理(主页已先落地当前方式:PyPI 卸载 + 删二进制 + `~/minecraft` 手动清理) |
| `README.md` | 同步:安装节改为单行命令,补充卸载节 |

## 6. 备选方案对比

| 方案 | 命令数 | 依赖 | macOS pip 问题 | 统一卸载 | 维护成本 |
|---|---|---|---|---|---|
| 现状:pip | 1 | Python 3.10+ | ✗ 不生效 | ✗ | 低 |
| 现状:二进制 | 下载+解压 | 无 | ✓ | ✗ 手工 | 低 |
| `uv tool install` | 2(需先装 uv) | uv | ✓ | `uv tool uninstall` | 低 |
| **本方案 curl\|sh + uninstall** | **1** | sh+curl | **✓** | **内置一条命令** | 中 |

`uv tool` 是最低成本的备选(官方自己就解决了 PEP 668),但要求用户已有 uv,不是「一条命令」,且卸载依赖 uv 而非工具自带。本方案为推荐项;`uv tool install orzmc-app` 保留为官网「其它方式」之一。

## 7. 落地计划与验收

**阶段一(Unix,本轮实现):**
1. `docs/install.sh`(含 `--uninstall`)。
2. 库:`InstallManifest` + `uninstall_self` + 测试(`orzmc/tests/`,fake fs,无网络)。
3. 公共 API 导出 `uninstall_self`。
4. 应用:`uninstall` 子命令。
5. `ci.yml` 新增 `installer` job(6 平台):`sh docs/install.sh` → `orzmc version` 断言版本 → `orzmc uninstall --yes` → 断言二进制已删除、rc 行已还原。
6. 官网 / README 更新。

**阶段二(Windows):** `docs/install.ps1` + Windows 卸载细节(运行中 exe 删除:PyInstaller onefile 的主 exe 通常可删;若被锁,记录待重启删除计划),并把 CI `installer` job 扩展到 Windows。

**阶段三:** 把本设计评审通过的架构结论写入 `AGENTS.md`,本设计文档降级为参考或归档。

**验收标准(每平台):**
- 全新环境 `curl -fsSL .../install.sh | sh` 一条命令装好,`orzmc version` 可用。
- 重复执行 = 升级,不产生重复 rc 行。
- `orzmc uninstall --yes` 后:二进制消失、rc 改动还原、游戏数据保留并提示。
- `--remove-root` 后:游戏数据一并清除。

## 8. 风险与注意点

- **GitHub API 限流**(60 次/时/IP):脚本识别限流响应并提示 `--version`;固定版本路径不触 API。
- **下载产物校验**:magic 校验防止 302 落到错误页;失败不安装。
- **PATH 幂等**:运行期 + rc 文件双重判重;卸载只删 manifest 记录的精确行,不动用户自己的编辑。
- **删除运行中二进制**:POSIX 下可安全删除已映射的二进制;Windows 若被锁需「重启后删除」计划(阶段二处理)。
- **卸载默认保留游戏数据**,删数据必须显式 `--remove-root` / 确认——与 `orzmc remove` 的确认模式一致。
- **`~/.local/bin` / state 目录为共享目录**:卸载只 `rmdir` 空目录,不整目录删除。
- **脚本可移植性**:`install.sh` 严格 POSIX(仅 `sh` 可跑),这是 macOS `/bin/sh` 是 bash 3.2、Linux `/bin/sh` 是 dash 的硬约束。
