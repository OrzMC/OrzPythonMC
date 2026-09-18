# AGENTS.md — OrzMC 项目事实来源

> 本文件是本仓库的**单一事实来源**。Claude Code / Codex / Cursor / Copilot 等任何 AI 智能体在动手前必须先读本文件。
> 项目有版本漂移风险时,优先依据本文件,而不是旧代码或记忆。若发现本文件与代码不符,请先更新本文件并同步代码。

## 项目定位

OrzMC 是一个跨平台 Minecraft **客户端启动 / 服务端部署** CLI 工具。**应用 + 库**双层结构:

- **`orzmc`(库)**:可复用能力,独立发布到 PyPI,高测试覆盖。零框架依赖(仅 requests / rich)。
- **`orzmc-app`(应用)**:typer CLI(rich 交互提示),提供 `orzmc` 命令。只调用库的**公共 API**,不触碰库内部实现。

用户最终体验:无参 `orzmc` 打印帮助;`orzmc client/server` 等直接命令行使用;版本缺失自动安装;Java 运行时沙盒托管在应用目录下,不依赖系统 java。

**许可证为 Apache-2.0**(唯一权威:根 `LICENSE` 文件)。两个包 pyproject 的 `license` 字段与官网文案必须与此一致——2026-08 曾因 pyproject/官网误写 MIT 与根 LICENSE(Apache-2.0)不一致而统一修正过,勿再改回。

## 技术选型

| 项 | 选择 | 说明 |
|---|---|---|
| 包管理 | **uv**(workspace) | 根 pyproject 声明成员;`uv.lock` 提交入库,保证可复现 |
| 构建 | hatchling | 版本动态读取:库 ← `orzmc/version.py`,应用 ← `orzmc_app/__init__.py`(两 pyproject 均不写死版本) |
| Python | `>=3.10` | 工具链固定 **3.12**(`uv python pin 3.12`) |
| CLI | typer | 子命令结构;无参打印帮助 |
| 交互选择器 | **prompt_toolkit**(应用层) | 全屏键盘导航 TUI;`input=`/`output=` 可注入,测试用 `create_pipe_input`+`DummyOutput`(无真实终端) |
| 测试 | pytest + ruff + mypy | 库测试不打真实网络 / 系统 java |

## 架构与依赖方向

```
orzmc_app(应用:cli/)  →  orzmc 公共 API
orzmc/services → orzmc/core → orzmc/domain + orzmc/infra
```

- **依赖只允许单向向下**:`domain` 与 `infra` 最底层;`core` 适配外部 API(Mojang 元数据、Fabric/Forge 附加件、服务端核心策略);`services` 编排用例。
- **客户端/服务端核心策略(对称镜像)**:`core/server/` 定义 `CoreProvider` 抽象 + `ServerPrepare` 注入接口,`vanilla/paper/fabric/forge` 四个 provider 自注册;`core/client/` 定义 `ClientProvider` 抽象 + `ClientPrepare` 注入接口,`vanilla/fabric/forge` 三个 provider 自注册(paper 无客户端,返回 `None`)。`ClientService`/`ServerService` 只按 `GameType` 分发,**改一种类型不影响其它类型实现**。各 provider 的 Forge/Fabric 复杂度收敛在各自文件内;`core` **不 import services 层**——`download`/`resolve_build_java` 等编排 seam 由 services 注入(依赖倒置),Provider 内只依赖 domain + infra。
- **Forge 用 Maven API**:`core/forge.py` 以 `promotions_slim.json` 解析 `<mc>-<build>` 版本、下载官方安装器;客户端/服务端 provider 共用。客户端启动定义嵌在安装器内 `version.json`,用 `zipfile` 读取(无需运行安装器);服务端用 `--installServer` 安装。不再做 HTML 抓取。
- **进度协议(两类任务)**:`ProgressSink.start(desc, total)` 是**计数任务**(几千个资源文件,`advance(n)` 加 n 个),`ProgressSink.start_bytes(desc, total)` 是**字节任务**(单个下载,`advance(n)` 加 n 字节);后者是**非抽象**方法、默认转调 `start`,所以第三方 sink 不实现也能工作,只有 `RichProgress` 覆写它来换列。两者必须分开:rich 的 `TransferSpeedColumn` 把速度格式化成「每秒多少字节」,用在文件计数上就是胡说。`RichProgress` 因此按任务类型**在 start 前换 `progress.columns`**(计数:描述+条+计数+百分比+耗时+转圈;字节:描述+条+`23.4/39.2 MB`+`4.1 MB/s`+剩余时间+转圈)。写测试要注意:rich 的速度来自**采样队列**(`Task.speed` 用首尾样本时间差估),只有一次 `advance` 时 `total_time == 0` → 渲染成 `?`,测试里要两次带间隔的 `advance` 才能断言 `B/s`。`status(desc)` 仍是「不定长」(`start(desc, None)`)。**协议解耦**:`orzmc/infra/log.py` 定义 `Reporter`,`orzmc/infra/progress.py` 定义 `ProgressSink`。库内置 rich 默认实现(`RichReporter`/`RichProgress`)。**禁止**库内直接 `print` / `os.system`。`RichProgress` 的 live 判活必须用 `Progress.live.is_started`(`live` 是 Live 实例、恒真,旧 `if not live` 导致 live 永不 start、进度条从不渲染);`finish()` 在任务清空后主动 `stop()` 收掉 live 区,避免后续 plain 日志与残留清行序列互相干扰;字节计数列 `_count_column()` 对 `total is None` 渲染空串,纯文本渲染函数可单测。
- **元数据缓存与刷新(统一策略)**:`orzmc/infra/cache.py` 的 `MetadataCache` 是唯一缓存层——Mojang 版本清单、version JSON、fabric-meta、Paper Fill API、Forge promotions 全部走它。策略三条:**TTL 24h**(`DEFAULT_TTL`,按文件 mtime + 注入时钟 `now` 判定,`ttl<=0` 表示永不复用);**`refresh=True` 绕过所有缓存读**(CLI `--refresh` → `RuntimeOptions.refresh` → `Services.cache`,`for_version` 也保留);**拉取失败时回退到磁盘上(可能过期的)旧副本**并 warn,只有完全没有缓存才抛错。判定细节:过期条件是 `-_FUTURE_SKEW(300s) <= now-mtime < ttl` —— Windows 上刚写入的文件时间戳可能比 `time.time()` 领先几毫秒(CI 曾因此只在 Windows 上缓存不命中),故容忍小幅未来时间;远超该幅度视为时钟异常、判过期。缓存文件:`cache/version_manifest.json`(沿用原路径)、`cache/versions/<v>.json`(内容寻址 + sha1 校验,refresh 也强制绕过)、`cache/meta/<adapter>/<key>.json`(`meta_path(*parts)` 生成,片段做可移植字符清洗)。名称语义:`update` 专指「升级 CLI 自身」(见下文 `orzmc update`),**不**用于元数据刷新。
- **下载进度统一**:`orzmc/infra/transfer.py` 的 `download_with_progress(http, url, dest, sink, desc)` 是唯一「带字节进度下载」入口(`Downloader.download_file`、`JavaEnv`、`Mojang.version_json` 共用);元数据 JSON 请求用 `ProgressSink.status(desc)`(默认 = `start(desc, None)`,不定长进度条),慢网不再静默等待。**总量取自流式 GET 的响应头**(`HttpClient.download(..., on_open=...)` 在写盘前回调 `Content-Length`),不再发探测用的 HEAD —— 那个 HEAD 每个单文件多一次往返(实测高延迟链路上 0.5-3s);`HttpClient.content_length()` 因此已删除。
- **下载并发与超时(实测结论,别重复调研)**:资源阶段(4000-5000 个 <50KB 的对象)耗时几乎完全由「**每请求延迟 乘 并发数**」决定,不是带宽 —— 实测同一台机器(经 TUN 代理,单请求中位 528ms)并发 8/16/24/32 分别为 8.7/15.8/20.3/23.3 req/s,32 线程约 2.2-2.7 倍。因此:`RuntimeOptions.download_threads`(`--download-threads/-j`,1-64,默认 16 `DEFAULT_DOWNLOAD_THREADS`);`Downloader(workers=...)` 传给 `ThreadPoolExecutor`;**`HTTPAdapter(pool_connections=pool_maxsize)` 必须 ≥ 并发数**(`Services` 里 `max(DEFAULT_POOL_SIZE, download_threads)`)—— 池小于并发时 urllib3 丢弃 keep-alive 连接、每个请求重建 TCP+TLS,比复用慢 3 倍。小文件批量走 `SMALL_FILE_READ_TIMEOUT=15s`(读超时是**单次 socket 读**的上限,不是总时长,所以不长档大文件不会因此被掐断),单文件/大文件用默认 30s。资源索引的 SHA-1 全量校验 480MB 只要 1.19s(404 MB/s)、rich 进度渲染 2µs/次、连接复用本身正常(8 线程 120 请求只建 8 条连接)——**这三项都不是瓶颈**。**大文件分块并行**(批次 2):`download_with_progress(..., parts=N)` 对 ≥`PARALLEL_MIN_SIZE`(8MB)的文件分 N 路 `Range` 取再拼接。实测中位数(交错多轮,单发测量在这条链路上完全不可信:同一文件同一模式在 1.0-5.0 MB/s 之间跳):piston-data 39MB 客户端 jar 单流 1.93 / 2 路 3.19 / **4 路 4.07 MB/s**;Adoptium JRE 42MB 1.12 / 1.71 / **1.86**;libraries.minecraft.net 24MB 0.70 / 0.66 / **0.79** —— 故 `PARALLEL_PARTS=4`(8 路无额外收益)。首块(`_PROBE_SIZE`=1MiB)就地取并兼作「服务器是否支持 Range」探针:返回 206 就继续并行取剩余,返回 200(代理/镜像忽略 `Range`)说明该响应就是完整正文,直接按单流写完 —— 既不多发探测请求,也不会出现「每个分块各下一份完整文件」;拼接完成后校验总字节数,` .part*`/`.tmp` 在 `finally` 里清理,上游还有 SHA-1 兜底。进度由**调用线程**独占喂给 `sink`(工作线程只做原子累加),避免多线程渲染。接线:`Downloader._parts`/`JavaEnv(parts=)`(均随 `download_threads` 走,`-j 1` 即关掉分块)与 `SelfUpdater`(自升级二进制 16MB)。第三方镜像(如 BMCLAPI)实测**更慢**(同一台机器上中位延迟 2.9-3.4s,是 Mojang 的 5 倍,因为它每请求先 302 跳转),已明确不做。`RichProgress` 列:描述 + 条 + 计数 + 百分比(`_percentage_column`,`total is None` 时渲染空串)+ 耗时 + 转圈;不显示速度是有意的 —— 同一个 task 既承载字节数也承载文件数,速度列必有一边是错的。
- **路径纯函数**:`PathLayout`(domain)只拼路径、**不建目录**;建目录统一在 service 内 `fs.ensure_dir`。
- **Java 沙盒**:运行时安装在 `<root>/java/<major>/`,用 `bin/java` 启动;版本要求读自版本 JSON `javaVersion.majorVersion`(缺失默认 8)。JRE 即可满足所有类型运行,无需完整 JDK。
- **服务端启动与关闭**:`server` 有独立 `--nogui` 选项(无窗口;老用法 `--server-args nogui` 仍兼容,`_build_server_command` 判定重不重复注入)。启动后子进程继承父进程 stdin,终端输入 `stop` 即保存退出;Ctrl-C 由 `ProcessRunner.run_stream` 优雅回收——子进程同在前台进程组也收到 SIGINT,父进程捕获 `KeyboardInterrupt` 后等待其保存退出(关闭日志经 `on_line` 透传),超 60s 未退先 SIGTERM 再 SIGKILL,最后 re-raise 让 CLI 报"已取消"返回 130,不遗留孤儿进程。
- **交互式版本选择**:公共 API `remote_version_catalog` + `VersionEntry`(id+type,`channel` 分 release/snapshot,非 release 全归 snapshot)提供全量版本清单。应用层选择器分两层:`orzmc_app/cli/picker.py` 的 **`PickerState`**(纯状态机,无 I/O,可单测——`catalog/channel/query/index/start/rows`,`items` 按 query 或 channel 过滤,`move/jump` 夹紧并滚动视口保光标可见,`toggle` 切通道并复位,`set_rows` resize 视口保光标可见)+ **`run_picker(catalog, *, input, output)`**(prompt_toolkit 全屏 TUI——↑↓ 选择版本、←→/PgUp/PgDn 前后翻页、Home/End 跳首尾,默认视口 10 行、光标初始在最顶(最新),输入在当前列表内即时过滤(命中片段黄色高亮、非 release 尾带灰色类型标记;**搜索不过通道**,想搜测试版/远古版本先 `t` 切过去),`t` 仅搜索框空时切通道(否则 `24w14potato` 这类含 't' 的版本无法搜索),`x` 清过滤,Esc 返回 None 用最新,Ctrl-C 抛 `KeyboardInterrupt`);`orzmc_app/cli/prompts.py` 的 `resolve_version` 在 TTY 分支调用 `run_picker`,异常时黄条警告回退最新;同文件 `resolve_username`:`client` 未指定 `-u/--username` 且 TTY 时用 `Prompt.ask` 交互询问玩家名(回车默认 `guest`),非 TTY 静默用默认。关键绑定(方向键/escape/t/x)均 `eager=True` 抑制默认行为;`←/→` 翻页与 `t` 一样**仅搜索框空时生效**,有查询时左右键保留给搜索框移动光标;`merge_key_bindings` 合并默认编辑键供搜索框退格/输入使用。**响应式布局**:纯函数 `_layout(term_rows, term_columns) -> LayoutSpec(view_rows/help_lines)` 静态预留 title+↑+↓+search 槽位(`_FIXED_ROWS=4`,`dont_extend_height` 空槽折叠);底部帮助拆成**多行短句** `_HELP_LINES`(每条都说明按键的作用,按显示宽非递减排序,列宽只放得下几条就渲染前几条,行高预算保列表 ≥`_MIN_VIEW_ROWS=3` 行),`help_lines=min(列宽可容纳条数, 行高预算)`,`view_rows=max(1, min(10, rows-4-help_lines))`;选中行**纯反显高亮(无指针)**,超视口显示「↑/↓ 还有 N 个」滚动指示;每个渲染片段与按键 handler 先 `_sync_layout()` 读实时 `get_size()` 再 `set_rows`,故运行中 resize(SIGWINCH)自动跟随。纯渲染函数(`title/list/up/down/help_fragments`)无 I/O 可直接单测。

## 目录结构

```
python/                         # uv workspace 根
  pyproject.toml  uv.lock  AGENTS.md  README.md
  .github/workflows/{ci,acceptance,release,release-please,pages}.yml
  .github/{dependabot.yml,PULL_REQUEST_TEMPLATE.md}  release-please-config.json
  .release-please-manifest.json  CHANGELOG.md(机器人维护)  CONTRIBUTING.md
  scripts/{build,acceptance,accept_selfupdate}.py
  docs/index.html                # 官网(静态单页,GitHub Pages 托管;最新版 tag 部署期注入)
  docs/install.sh  install.ps1   # 一键安装器(Unix sh / Windows PowerShell)
  docs/installer-design.md       # 安装器方案设计(历史评审稿,参考)
  orzmc/                        # 库包(name="orzmc",py.typed)
    pyproject.toml
    orzmc/  version.py  __init__.py
            domain/  infra/  core/  services/
            core/     mojang.py  fabric.py  forge.py  profiles.py
                      client/   # ClientProvider 策略:vanilla/fabric/forge
                      server/   # CoreProvider 策略:vanilla/paper/fabric/forge
    tests/
  orzmc_app/                    # 应用包(name="orzmc-app")
    pyproject.toml
    orzmc_app/  cli/  __init__.py
    tests/
```

游戏根目录(默认 `~/minecraft`,可配置),统一多版本并存布局,由库内 `PathLayout` 负责:

```
<root>/
  versions/<mc_version>/          # 一版本一目录,客户端/服务端并存
    client/  assets/  libraries/  natives/  profiles/
             <version>.jar  launcher_profiles.json
    server/<server_type>/         # vanilla|paper|fabric|forge
      <core>.jar  eula.txt  server.properties  commands.yml  world/  plugins/
  java/<java_major>/              # 托管 JRE/JDK,不依赖系统 java
  cache/version_manifest.json  cache/versions/  cache/download_tmp/
  backup/worlds/  backup/music/<v>/
```

## 一键安装 / 卸载(两层安装器)

**设计**:平台专用脚本负责「下载 + 落盘 + 登记 PATH + 写安装记录(manifest)」;平台无关的 `orzmc self-uninstall` 内置命令负责「读 manifest → 还原 PATH → 删二进制 → 清理空目录」。脚本与命令都只删/改自己登记过的东西,不动用户已有内容。卸载只 `rmdir` 空目录,绝不 rmtree 共享目录(如 `~/.local/bin`)。

**安装记录 `install.conf`(key=value 格式,非 JSON)**:
- 位置:Unix `${XDG_STATE_HOME:-$HOME/.local/state}/orzmc/install.conf`;Windows `%LOCALAPPDATA%\orzmc\install.conf`(库侧 `default_state_dir()` 是 CI 隔离的关键接缝)。
- 字段:`tool schema version platform install_dir binary source path_file path_line root_dir`;可选字段为空则不写行。Windows 不写 `path_file`,`path_line` 记 install_dir token(卸载只按 token 从 User PATH 移除)。
- **PS 5.1 `Set-Content -Encoding UTF8` 写 BOM**,库侧读取一律 `utf-8-sig`。

**最新版解析的限流兜底**:两个安装器与库都首选 GitHub REST API(`api.github.com/.../releases/latest`,60 次/时/**IP**),失败或限流时退回 `https://github.com/<repo>/releases/latest` 的 **302 `Location`**(非 API 端点,不限流),再自行拼资产地址。CI runner 与共享 NAT 用户必然吃限流,这条兜底是产品可用性路径而非可选优化。`install.sh` 用 `curl -sS -o /dev/null -w '%{redirect_url}'`(**不能带 `-L`**:跟随完重定向后 `%{redirect_url}` 是空串);`install.ps1` 用 `[System.Net.WebRequest]::Create(...).AllowAutoRedirect = $false` 读 `Location`;库侧 `HttpClient.head_location()` + `selfupdate._tag_from_redirect()`。两个安装器另有 `ORZMC_API_LATEST` 接缝(测试/镜像):指向不可用地址即可验证兜底,`scripts/accept_selfupdate.py` 有对应 case。

**`docs/install.sh`(严格 POSIX sh)**:`set -eu`;无数组 / 无 `[[ ]]` / 无 `&>` / 无 `sed -i` / 无 jq,环境变量一律 `${VAR:-default}`。下载产物 magic 校验(`od -An -tx1 -N4`:Mach-O `cffaedfe` / ELF `7f454c46`;HTML/JSON 拒绝)。PATH 登记用 `$SHELL` 选 `.zshrc`/`.bashrc`/`.profile`,运行期 `case ":$PATH:"` 判已在 + `grep -Fqx` 判行重复,卸载用 `grep -Fvx` 删精确行(仅当行存在才写回)。选项:`--version vX.Y.Z`(固定版本,绕过 GitHub API 限流)/ `--dir` / `--no-modify-rc`(或 `ORZMC_NO_RC=1`)/ `--file`(本地安装,测试接缝)/ `--uninstall`(shell 兜底)。

**`docs/install.ps1`(PowerShell 5.1+)**:
- **不用 `param()` 块**(兼容 `irm | iex`,脚本内容被求值时无参数表),参数从 `$args` 手工 token 解析;共享状态统一 `$script:` 前缀,保证 `-File`(脚本作用域)与 iex(调用方全局作用域)两调用方式行为一致。
- **不显式 `exit`**(iex 下会连宿主 PowerShell 窗口一起关);错误用 `die` → `throw`(`-File` 下未捕获异常退出码 1,iex 下只报错、宿主窗口保留)。
- 开头保存、`finally` 恢复 `$ErrorActionPreference`/`$ProgressPreference`,避免污染 iex 宿主全局状态。
- **编码自愈(PS 5.1 中文乱码)**:GitHub Pages 对 `.ps1` 返回 `application/octet-stream`(无 charset),PS 5.1 的 `irm` 会按 Latin-1 **逐字节解码**,每个 UTF-8 字节变成一个乱码字符(功能正常、仅中文显示乱码;pwsh 按 UTF-8 正确解码)。脚本顶部用中文探测串 `'已安装到'` 探测——若不含 CJK 字符(≥ U+2000)即已被误解码,则触发自愈:重抓自身 URL(`ORZMC_INSTALL_URL` 可覆盖)→ 逐字节字符反转回 UTF-8 字节 → 恢复原始源码 → `& ([scriptblock]::Create($__clean)) @args` 带原参数重新执行。pwsh / `-File`(带 BOM)探测为干净,零开销;重抓失败降级继续(乱码但功能正常)。**不要移除顶部自愈块**;它是「不改一键命令地址」时唯一能治好 PS 5.1 乱码的机制。
- 架构探测:`PROCESSOR_ARCHITEW6432`(32 位 PS 取真实架构)兜底 `PROCESSOR_ARCHITECTURE`;下载产物校验 PE 头 `MZ`;PATH 追加 User env var(`;` 分 token 判重)。
- **已知坑**:`Join-Path $null "x"` 在 EAP=Stop 下仍是非终止错误(执行继续、退出码 0),临时文件路径一律用 `[System.IO.Path]::GetTempPath()` 拼接;`.NET` 在 macOS 上 `SetEnvironmentVariable('Path', ..., 'User')` 是 no-op(真实 PATH round-trip 只能在 Windows CI 验证)。
- 命令:`irm https://orzmc.github.io/OrzPythonMC/install.ps1 | iex`;选项 `-version/-dir/-no-modify-rc/-file/-uninstall/-help` 与 sh 对齐。

**`orzmc self-uninstall`(库 `orzmc/services/selfinstall.py`,公共 API `uninstall_self`)**:`InstallManifest`(frozen dataclass,`write/read/from_lines/find`;`find` 先 state 目录 `install.conf`,再二进制旁 `.orzmc-manifest` 兜底)。`SelfUninstaller.uninstall` 顺序:**安全护栏 → 读 manifest → 还原 PATH → 删二进制 → rmdir 空目录 → 删 manifest → 游戏数据**(`--remove-root`/`--yes`/交互 confirm 控制,默认保留)。
- **安全护栏**:路径含 `.venv`/`venv`/`env`/`site-packages`/`dist-packages`/`_MEI*`,或 `_MEIPASS` 同目录 → 拒绝(`RuntimeError`),`--force` 放行——防误删开发环境 / 已解包的 PyInstaller 产物。
- **pip 托管检测**:二进制旁有 `*.dist-info` 或路径含 site-packages → 不删二进制,提示「请用 pip uninstall orzmc-app 卸载」。
- **PATH 还原**:Unix 从 `path_file` 删精确 `path_line` 行;Windows 从 User PATH(winreg,stdlib,guard import)移除记录 token 并广播 `WM_SETTINGCHANGE`,失败只 warn。
- **PyInstaller onefile 自删除限制**:删除正在运行的 onefile 可执行文件后,任何后续 PYZ 懒加载 import 都会 `SystemExit`——**二进制删除必须是最后一个操作**(所有 reporter 输出之后)。Windows 锁定时 `rename` + `MoveFileExW(MOVEFILE_DELAY_UNTIL_REBOOT)` 兜底并提示重启后删除。

**`orzmc update`(库 `orzmc/services/selfupdate.py`,公共 API `check_self_update` / `update_self`)**:升级的是**工具自身二进制**。名称语义已固定:`update` = 自升级 CLI,`self-uninstall` = 卸载,`remove`/`list` = Minecraft 版本。流程:护栏 → 解析目标版本 → 下载/校验 → **交接给分离助手在父进程退出后替换二进制** → 重写 `install.conf`。
- **为何必须延迟替换**:PyInstaller onefile 的 PYZ 归档是**按需**从 `<可执行文件>?<offset>`(引导器写入的 `sys._pyinstaller_pyz`)读取的,运行中替换自身会让下一个尚未 import 的模块读坏归档(真机复现 `zlib.error: incorrect header check`);Windows 还禁止覆盖/删除运行中的 exe。所以 `_handoff` 用 `ProcessRunner.run_detached` 拉起助手,助手是**唯一**执行最终替换的角色。
  - POSIX:`/bin/sh` 轮询 `kill -0 <pid>`(必须等父进程退出:`mv` 对运行中的二进制**会成功**)后 `mv -f`,末尾 echo `DONE`/`FAILED`。
  - Windows:`powershell` **不做存活轮询**——系统本身就拒绝替换运行中的 exe,所以「重试到成功」既安全又可靠;按 `Move-Item → [IO.File]::Copy(overwrite) → 改名绕开再搬` 三级兜底(CI 实测 move 会失败、copy/rename-aside 各成功过一次)。`Move-Item` 必须带 `-ErrorAction Stop`,否则失败是 non-terminating error,catch 不触发、兜底永不执行、暂存文件被静默留下(CI 抓到)。
  - **Windows 必须用 `CREATE_NO_WINDOW` 而非 `DETACHED_PROCESS`**(`run_detached(..., windows_no_window=True)`):`powershell.exe` 这类控制台宿主在完全没有控制台时起不来,表现为 Popen 成功但助手一行代码都没执行(CI 上助手日志全空)。
  - 助手**自己**把过程写进 `<install_dir>/.orzmc-update.log`(Windows 用 `Add-Content`,POSIX 用 stdout 重定向)——PowerShell 会缓冲重定向的 stdout,靠 stdout 无法判断助手是卡住还是死了;每次交接前先删旧日志(残留 `DONE` 会被误判为本次结果)。
  - 故 `UpdateCheck.applied=True` 的含义是「已暂存并交接」,**命令退出后才生效**。
- 暂存固定 `<install_dir>/.orzmc-update.tmp`(与目标同卷 → rename 原子);交前校验 magic(ELF/Mach-O/PE,HTML/JSON 一律拒绝);失败清理暂存、旧版本原样保留。
- **当前版本以运行中二进制的 `orzmc/version.py` 为准**(`current_version()`);`install.conf` 只是安装记录(可能是 `local-build`,或被「已交接未落地」提前写上)——用内嵌版本判断才能自愈:交接失败时下次仍会重试。
- 选项/回退:无参走 GitHub API `releases/latest`(未认证 60 次/时/IP;环境里有 `GITHUB_TOKEN` / `GH_TOKEN` 时自动带上 Bearer 头 → 5000 次/时,CI 已注入 `secrets.GITHUB_TOKEN`),失败提示 `--version vX.Y.Z`(指定版本完全不碰 API);`--check` 只查询;`--file <本地二进制>` + `--version`(离线 / CI 接缝);`--yes` 跳过确认;`--force` 仅绕过开发环境护栏(pip/pipx 托管**始终**拒绝,提示 `pip install -U orzmc-app`)。
- **两个 PowerShell 都要验收**:安装器声明 5.1+,而 Windows 用户默认拿到的是 5.1(`powershell`,字节级误解码 octet-stream 的 `.ps1`)、现代环境是 7.x(`pwsh`)——两者在编码、iex 作用域、`-UseBasicParsing` 语义上并不一致,故必须分别真机跑,不能只测「5.1 子集写法」。harness 用 `--powershell <exe>` 选择 shell、`--expect-ps-major {5,7}` 断死主版本(否则机器上 `powershell` 被换成 7 会假通过),并打印 `$PSVersionTable` 作为证据。自升级助手固定在 Windows 用 `powershell`(5.1 一定存在,`pwsh` 不保证),与「用哪个 shell 跑安装器」是两件事。
- 本地/CI 验收:`scripts/accept_selfupdate.py`(跨平台 stdlib,`uv run python scripts/accept_selfupdate.py [--skip-build] [--skip-download] [--skip-oneline] [--keep] [--powershell pwsh --expect-ps-major 7]`)——覆盖 PowerShell 运行时信息、安装器、`--check`、`--file` 延迟替换真的落地(sha256 对比)、真实下载、失败不破坏旧文件、venv 护栏、**管道入口**(`irm \| iex` / `curl \| sh`,用内置 loopback HTTP 服务模拟 GitHub Pages 的 `application/octet-stream`,断言中文未被误解码=编码自愈生效)、卸载闭环,详见 CI `installer` job。

**CI `installer` job(ci.yml)**:push-only(发版 tag 与 PR 不跑,同 `binary`),6 平台矩阵,`needs: quality`。**鸡生蛋**:最新已发布二进制尚不含 `self-uninstall`/`update`,e2e 必须本地构建 + `--file` 接缝,不能拉 release。harness 把 `docs/` 复制到沙箱并按 `pages.yml` **剥掉 install.ps1 的 BOM** 再对外提供(`serve_docs` 必须服务**部署形态**:带 BOM 的源码经 `irm` 求值时首行 U+FEFF 会被当命令,报 "The term 'Windows' is not recognized"——正是 d4e049b 修过的坑;同时断言「仓库保留 BOM(`-File` 需要)」与「部署形态无 BOM」这对不变式,pages.yml 若不再剥 BOM,CI 立刻红)。harness 自己解析参照 tag 走 `https://github.com/<repo>/releases/latest` 的 302(不碰 API、不限流),`--check` 的「显式版本」分支严格断言、「裸 API」分支遇限流只 WARN(库的正式回退就是 `--version`);所有 PowerShell 子进程都加 `[Console]::OutputEncoding=UTF8` 前导并把 `throw` 转成 exit 1,harness 自身 stdout/stderr 也强制 UTF-8(Windows 控制台默认 cp1252,否则第一个中文 `print` 就 UnicodeEncodeError)。`Build binary` 后用**一个跨平台 harness** `scripts/accept_selfupdate.py --skip-build`(Unix 跑一次;Windows **跑两次**——`--powershell powershell --expect-ps-major 5` 与 `--powershell pwsh --expect-ps-major 7`,分别断言 PS 5.1 / 7.x)。步骤:PS 运行时信息 → `install --file` 到临时目录 → `update --check` → `update --file <系统可执行文件> -v v9.9.9`(**轮询 sha256 证明助手真的落地** + 暂存文件消失 + 记录改写)→ `update -v <最新 tag>`(真实下载)→ 不存在版本失败不得破坏旧文件 → venv 路径被护栏拒绝 → **管道入口**(内置 loopback HTTP 服务把 `docs/` 按 GitHub Pages 的 Content-Type 提供;Windows 跑**两种**:`irm` + `[scriptblock]::Create` + `-file/-dir`(不下载资产)与**字面 `irm … \| iex`**(靠 `ORZMC_BIN`/`ORZMC_NO_RC`/`ORZMC_ROOT_DIR` 环境接缝改道,含真实 release 下载);Unix 走 `curl \| sh -s --`;断言退出码、二进制、中文未被误解码、独立安装记录、**未登记 PATH 改动**(看 `install.conf` 里没有 `path_line`/`path_file`,而不是看提示文案))→ `self-uninstall` 删二进制 + 记录且不删游戏数据(Windows 上运行中的镜像删不掉,按文档接受 rename + 重启后删除并校验该提示)。harness 内部把 state 重定向到自己的临时目录(`XDG_STATE_HOME` / `LOCALAPPDATA`,`ORZMC_ROOT_DIR` 把游戏根也关进沙箱)并传 `--no-modify-rc` / `ORZMC_NO_RC=1`,PATH / rc / 用户目录一律不碰。

## 常用命令

```bash
uv python pin 3.12                  # 固定工具链
uv sync --all-packages              # 安装全部成员 + 开发依赖(首次 / 依赖变更后)
uv run ruff check .                 # lint
uv run ruff format --check .        # 格式检查
uv run --all-packages pytest        # 全部测试
uv run mypy                         # 类型检查
uv run orzmc --help                 # 应用子命令树(无子命令时同样打印帮助)
uv run orzmc version                # 打印版本(读取库 version.py)
uv run orzmc update --check         # 检查是否有新版本(只读,--check 不受开发环境护栏限制;真正执行 update 在 venv 内会被拦)
uv build --all-packages            # 构建 sdist+wheel(库与应用)
uv publish                         # 发布到 PyPI
uv run --package orzmc-app python scripts/build.py   # PyInstaller 单文件二进制 → dist/
uv run python scripts/accept_selfupdate.py           # 安装器 + 自升级本地验收(跨平台,--skip-build 复用 dist/)
uv run python scripts/accept_selfupdate.py --skip-build --powershell pwsh --expect-ps-major 7   # Windows:再验一遍 PS 7
uv lock                            # 锁定依赖
```

## 代码规范

- **ruff**:`select = E,F,W,I,UP,B,SIM,C4,RUF`,`line-length = 120`;isort first-party = `orzmc, orzmc_app`。
- **mypy**:`check_untyped_defs`,`no_implicit_optional`。
- **pytest**:库测试用 **fake `Reporter`/`ProgressSink`/`HttpClient` + 真实 tmp 目录**(见 `orzmc/tests/fakes.py`),直接打公共 API 与 domain;不碰网络、不碰系统 java。测试文件放 `orzmc/tests/`、`orzmc_app/tests/`。
- **导入规范**:库内按层引用(`from orzmc.domain...`);应用只 `from orzmc import ...` 公共 API。
- **异常**:网络 / 文件错误在 service 层统一捕获并转 `RuntimeError`(中文消息),不裸抛 requests 异常。
- **类型注解**:公共 API 全量注解;`from __future__ import annotations` 开头。

## 迭代流程(trunk-based + release-please)

- **只有 `main` 是长期分支**(trunk),永远可发版;干活开短命分支(`feat/*`、`fix/*`、`docs/*`),用 PR 合入,**squash only**。`main` 有分支保护:必需检查 `quality` + `test (ubuntu-latest, x86_64)`,允许 admin 绕过(单人维护不被锁死)。
- **提交信息必须是 Conventional Commits**(`feat/fix/docs/chore/perf/test/ci/refactor`,可带 scope;破坏性变更 `feat!:` 或正文 `BREAKING CHANGE:`)。这不是风格问题:`release-please` 靠它推导版本号与 `CHANGELOG.md`,写错等于发版出错。
- **合前重验收的正规入口**(不要为了验收去改 `ci.yml` 的 `on.push.branches` —— 那种临时改动容易误合进 main,历史上真这么干过):
  ```bash
  gh workflow run ci.yml --ref <branch> -f full=true   # 追加 6 平台 binary + installer
  ```
  `ci.yml` 的 `binary`/`installer` 条件为 `(push 到分支) || inputs.full == true`;PR 默认只跑 `quality` + 6 平台 pytest,保持快速。
- **版本号不手改**:`orzmc/version.py` 与 `orzmc_app/orzmc_app/__init__.py` 都带 `# x-release-please-version` 标记,由 release-please 的 release PR 一起 bump(两个文件锁步,`test_app_and_library_versions_match` 守着);`release-please-config.json` 用 generic updater 改这两处,`.release-please-manifest.json` 记录上一次发布的版本。因为两个 pyproject 都是 `dynamic`,`uv.lock` **不记录本地包版本**,release PR 不需要动 lock。
- **版本策略(预稳定期)**:2.x 的破坏性 API 变更**不用** `feat!`/`BREAKING CHANGE:`(release-please 会直接推 major),走 minor + 在 `CHANGELOG.md` 里人工注明;等 API 冻结为 3.0.0 时再启用 `!`。
- 依赖与 Actions 升级交给 `.github/dependabot.yml`(每周一,分组);`CONTRIBUTING.md` + PR 模板给人和 AI 智能体同一份清单。
- **发布链路的一致性由测试锁死**(`orzmc_app/tests/test_release_consistency.py`):官网资产名 == 库 `asset_for()` == `release.yml` 矩阵产物名(去 `.exe`)、下载基线一致、pages 注入步与占位符成对、tag/版本护栏仍在、两处版本文件的 `x-release-please-version` 标记与 release-please 的 `extra-files` 一致。改任一处而漏改其余,测试即红。
- 夜间验收失败会自动开/追加 issue(`acceptance.yml` 的 `notify` job),不再依赖人盯。

## 改动流程

新增 / 修改能力时必须遵循:

1. **先在库实现 + 测试**:在 `orzmc/` 对应层改代码,`orzmc/tests/` 补用例;`uv run --all-packages pytest` 全绿后再进应用层。
2. **再暴露公共 API**:把新能力加入 `orzmc/__init__.py`(公共 API 即契约,改动要谨慎)。
3. **应用层调用**:`orzmc_app/cli/` 只调用公共 API,不 import 库内部模块。
4. **跑全部门禁**:`ruff` → `mypy` → `pytest` → `uv build` 全过。
5. **更新本文件**:涉及架构 / 命令 / 目录结构 / 规范的变更,同步更新 AGENTS.md(并考虑 README)。
6. 关键决策记录在本文件,避免各智能体行为漂移。

## CI / 真实验收

**6 平台矩阵**(多处复用同一组 runner 标签):`ubuntu-latest`、`ubuntu-24.04-arm`、`macos-15-intel`、`macos-15`、`windows-latest`、`windows-11-arm`。注意 **`macos-13` 已废弃**,x86_64 macOS 用 `macos-15-intel`;`macos-latest` 已迁到 macOS 26,arm64 显式钉 `macos-15`。arm64 runner 为 public preview。

- **`ci.yml`(push/PR + `workflow_dispatch`)**:`quality` 单 runner 跑格式/lint/mypy/测试/构建(平台无关);`test` 6 平台全跑 pytest(纯 Python 假件但覆盖 OS 敏感路径,秒级);`binary` 与 `installer` 在 `push` 到分支时跑,或用 `workflow_dispatch -f full=true` 在任意分支按需跑(发版 tag push 与普通 PR 不跑)——`binary` 6 平台 PyInstaller 构建 + 上传 artifact,`installer` 6 平台安装器 e2e(本地构建 `--file` 接缝 + 隔离环境,装→`version`→`self-uninstall`→断言二进制与 manifest 已删,见上文「一键安装/卸载」)。声明 `workflow_call` + `skip-test-matrix` input,供 release 复用。
- **`acceptance.yml`(每日 04:23 UTC + workflow_dispatch)**:失败时 `notify` job 自动开/追加 issue(`acceptance-logs-primary-*` / `-backcompat-*` 两套 artifact 名故意分开,同平台同名上传会冲突)。真实验收 harness `scripts/acceptance.py`(跨平台,stdlib + psutil 进程树管理,替代 `pgrep`/`pkill`;`-m orzmc_app.cli` 调 CLI,不嵌套 `uv run`)。
  - **版本策略(以最新版为主基准)**:`primary` job 夜间+手动,6 平台跑最新版 × 全部类型(server vanilla/paper/fabric/forge + client vanilla/fabric/forge);`backcompat` job 仅手动 `suite=full`,x86_64 三平台跑旧版本 vanilla 冒烟(`--backcompat`,默认 `1.20.4`)。`latest` 由 Mojang `version_manifest_v2.json` 的 `latest.release` 解析,不额外拉取其它源。
  - **判定语义**:`PASS`(server 日志 `Done (` / client 退出码 0 引导级);`UP(no Done)`(端口开 90s 无 Done = Mojang MC-263542 世界生成卡死,记警告不判失败);`SKIP`(日志含"不支持 Minecraft"/"未找到 Minecraft",类型暂未适配该版本,如 Forge 滞后);`UP(gap)`(上游无该平台产物:Adoptium 对某 OS/arch/major 的 Temurin 返回 404,或 Mojang 无该 arch 的 lwjgl natives —— 记警告不判失败,上游补齐后自动恢复真实判定);`FAIL`/`TIMEOUT` 判失败。客户端引导级判定依赖 Linux `xvfb-run`,headless 需装 xvfb;`--deep-client` 仅真机手动用(CI 的 macOS/Windows 无 GL 上下文会假阴性)。
  - 游戏 root 按 `runner.os`-`runner.arch` 缓存(Java + assets + jars),夜间只取增量。
- **`release.yml`(打 `v*` 标签)**:`quality` 复用 `ci.yml` 传 `skip-test-matrix: true`(6 平台 pytest 已在 main 跑过);`binary` 6 组合构建挂 GitHub Release;`pypi` 双包发布。
- **`pages.yml`(push main 且 `docs/**`/工作流自身变更、`release: published`、workflow_dispatch)**:**官网「最新版」在部署期注入** —— `index.html` 里是占位符 `__ORZMC_LATEST_TAG__`,部署前用**非 API 的 302**(`curl -sS -o /dev/null -w '%{redirect_url}'`,同样**不能带 `-L`**)解析最新 tag 后 `sed` 替换;浏览器端**不再调用 `api.github.com`**(60 次/时/IP,限流会让整个下载列表消失;读不到注入值时页面才退回 API,再失败才显示 Releases 链接),六个平台的下载地址按 `DOWNLOAD_BASE/<tag>/<asset>` 直接拼。`release: published` 触发是必需的:否则发版后官网会一直显示旧版本,直到下次 docs 变更。发布配置说明:静态官网(自包含单页 `docs/index.html`,无外部构建)用 `actions/configure`/`upload-pages-artifact`/`deploy-pages` 部署到 GitHub Pages,站点地址 <https://orzmc.github.io/OrzPythonMC/>;`permissions: pages: write + id-token: write`,`concurrency: group=pages` 防止并发部署互相踩。页面内下载小组件直接调 `api.github.com/repos/OrzMC/OrzPythonMC/releases/latest` 拉取最新发布,自动按平台给出下载链接 —— 改动 `docs/` 推送即自动更新。

## 发布

- **GITHUB_TOKEN 建的事件不会触发新工作流**(GitHub 的递归保护),这是本流程最容易踩的坑:release-please 用 `GITHUB_TOKEN` 建出的 tag **不会**触发 `release.yml` 的 `push: tags`,结果是「tag 与 Release 都在、但没有二进制、没发 PyPI」。因此 release-please 工作流在检测到「manifest 版本的 release 还没有产物」时**显式** `gh workflow run release.yml --ref main -f tag=vX.Y.Z` 接力(判据幂等:已有 `orzmc-linux-x86_64` 就什么都不做),`release.yml` 收尾发布后再 `gh workflow run pages.yml` 重新部署官网(官网"最新版"是部署期注入的)。两处都需要 `actions: write`。手动补发用同一入口;配了 `RELEASE_PLEASE_TOKEN`(细粒度 PAT)时 tag push 会自己触发,接力代码保留也无害(幂等)。
- `release-please-config.json` 里 `"draft": true`:Release 由 release-please 建为 **draft**,可见性交给 `release.yml` 的 `publish` 收尾(二进制 + PyPI 全部落地后才 `--draft=false`),避免"有 Release、没资产"的窗口期被 `releases/latest` 与官网看到。
- **人按按钮 = 合并 release PR**:`release-please.yml`(push main)让机器人维护 release PR(bump 两处版本 + 写 `CHANGELOG.md` + 更新 manifest);合并它 → 自动打 `vX.Y.Z` tag + 建带 notes 的 GitHub Release → 触发 `release.yml`。紧急时手动 `git tag vX.Y.Z && git push origin vX.Y.Z` 也走同一条流水线。
- `release.yml` 的 `verify` job 是**发版第一道护栏**:tag 必须等于 `orzmc/version.py` 的版本、且 tag 提交在 `main` 上。不一致会造成客户端自升级死循环(下载→替换→重启后版本没变→再提示升级)与错误的 PyPI 元数据。
- **原子发布**:`prepare` 先确保 Release 存在(手动 tag / 手动 dispatch 路径下用 `--generate-notes` 建 **draft**;release-please 已建好的原样不动)→ `binary` 用 `gh release upload --clobber` 挂 6 平台产物(不再用 softprops 之类会改写 notes/draft 状态的动作)→ `pypi` 双包发布 → `publish` 最后 `gh release edit --draft=false` 并触发官网重新部署。半成品(有二进制、没 PyPI 包)不会出现在 `releases/latest`,官网与一键安装不会提前推新版本。
- 双通道:**GitHub Release**(各平台 PyInstaller 二进制,见 `release.yml`)+ **PyPI**(`orzmc` 与 `orzmc-app` 双包)。
- PyPI 发布优先走 **Trusted Publisher(OIDC)**:`release.yml` 的 `pypi` job 带 **job 级** `permissions: {contents: read, id-token: write}`(job 级会覆盖工作流级,漏了 `id-token` 就换不到 token),且与 PyPI publisher 配置同为 `environment: release`;`scripts/publish_idempotent.sh` 用 **`--trusted-publishing always` 强制 OIDC**(`automatic` 在存在 token 时仍用 token,无法验证迁移是否成功),失败才回退 `UV_PUBLISH_TOKEN` 并打 `::warning::`(迁移期两条路都通)。PyPI 侧两个项目各自 Settings → Publishing → Add a new publisher(Owner/Repository/Workflow name=`release.yml`/Environment name=`release`)——**代码无法代办**;配好后跑 `gh workflow run release.yml --ref main -f check_oidc=true` 做预检(`release.yml` 的 `oidc-check` job,`check_oidc=true` 时其余发布 job 全部跳过):它用 `uv publish --dry-run --trusted-publishing always` **真的去换一次 OIDC token**(不上传文件),失败时还打印 PyPI 的原始响应(能点名不匹配的字段)。**预检必须在 `release.yml` 内**——PyPI 的 `Workflow name` 记的是文件名,放独立工作流文件里永远对不上(实测 `invalid-publisher`);本地跑必然报 `No OIDC token discovered`。确认发布日志出现「已通过 OIDC…」后,再 `gh secret delete PYPI_API_TOKEN_ORZMC_LIB PYPI_API_TOKEN_ORZMC_APP` 并删掉脚本里的回退分支与 release.yml 里的两个 `UV_PUBLISH_TOKEN` env。**两个包名不同**——glob 用 `dist/<包>-*`(`orzmc-*` 不误匹配 `orzmc_app-*`)。
- 版本号唯一源:`orzmc/version.py` 的 `__version__`。发版前提升它,并同步 **`orzmc_app/orzmc_app/__init__.py`** 的 `__version__`(应用版本的第二处、也是最后一处副本);两个 pyproject 都是 `dynamic = ["version"]` + `[tool.hatch.version] path`,分别从上面两个文件读取,`pyproject.toml` 里**不再**写死版本号(曾漏改过)。`orzmc_app/tests/test_cli.py::test_app_and_library_versions_match` 断言两者一致,漏改会红。
- **新包首版坑**:PyPI 禁止非用户身份(如 GitHub Actions 机器人)创建不存在的项目;`orzmc_app` 在 2.0.0 首次发布时不存在,须先由真实账号用 API token 手动上传一次创建项目,机器人之后才能自动发布后续版本。
- 可选升级:PyPI **Trusted Publishing**(OIDC)取代长期 token —— 两个包各配 trusted publisher(repo `OrzMC/OrzPythonMC`、workflow `release.yml`、environment `release`),CI 侧加 `permissions: id-token: write` 并把 `UV_PUBLISH_TOKEN` 换成 `uv publish --trusted-publishing always`;`RELEASE_PLEASE_TOKEN`(细粒度 PAT)可选,作用是让机器人开的 release PR 也触发 CI。
- CI 只做质量门禁与发布,**不**负责版本号管理(交给 release-please 的 release PR)。
