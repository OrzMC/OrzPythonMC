# Changelog

## [2.17.1](https://github.com/OrzMC/OrzPythonMC/compare/v2.17.0...v2.17.1) (2026-10-20)


### Bug Fixes

* **release:** 发布流水线也钉住 ORZMC_RUNTIME=python（断言步骤会直接跑二进制） ([#90](https://github.com/OrzMC/OrzPythonMC/issues/90)) ([91f234b](https://github.com/OrzMC/OrzPythonMC/commit/91f234bae9391ba94e980f359c46899f84bb1c7f))

## [2.17.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.16.0...v2.17.0) (2026-10-20)


### Features

* **runtime:** 新装默认走 Swift 真身（D-M3-1）+ 六个月维护窗口（D-M3-2） ([#87](https://github.com/OrzMC/OrzPythonMC/issues/87)) ([201de0c](https://github.com/OrzMC/OrzPythonMC/commit/201de0caa8cbe0c5156364b16951b2871e4c0847))

## [2.16.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.15.0...v2.16.0) (2026-10-20)


### Features

* **runtime:** 优先复用已有 Swift 原生安装（D-M3-3 混合方案）+ 库定位说明（D-M3-5） ([#86](https://github.com/OrzMC/OrzPythonMC/issues/86)) ([a4e65f4](https://github.com/OrzMC/OrzPythonMC/commit/a4e65f4be72b37a33341c2df74a711e40a6a115f))


### Bug Fixes

* **ci:** binary job 补检出父仓库（PyInstaller 资产的验收恢复真执行） ([#83](https://github.com/OrzMC/OrzPythonMC/issues/83)) ([0db8db4](https://github.com/OrzMC/OrzPythonMC/commit/0db8db44bb263073edf71bc7fb1ef1ad6a427c99))
* **ci:** frozen 验收命令改成单行（Windows runner 的 pwsh 不认反斜杠续行） ([#85](https://github.com/OrzMC/OrzPythonMC/issues/85)) ([ccf0080](https://github.com/OrzMC/OrzPythonMC/commit/ccf0080780de64317ed40ae9a15926861cc80b0c))

## [2.15.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.14.0...v2.15.0) (2026-10-20)

### 🧩 升级须知（可选：把 `orzmc` 换成 Swift 真身运行，M3 预览）

`2.15.0` 起 PyPI 的 `orzmc` 也能当**发行通道**：功能交给 Swift 实现，Python 只负责装/升级并转发。
**默认没变**（不设变量就还是原来的 Python 实现）。

- 试用：`export ORZMC_RUNTIME=swift`（PowerShell：`$env:ORZMC_RUNTIME="swift"`），
  首次运行自动下载真身（~13–35MB，SHA256 校验）到状态目录 `bin/`；
- 开启后 `orzmc --version` 报**真身**版本（如 `orzmc 0.18.0`），不是 PyPI 包版本；
- 预取 / 看状态：`orzmc runtime install` / `orzmc runtime status`；
- 回退：`unset ORZMC_RUNTIME`；断网且无本地真身 → `E_NETWORK`（不静默失败）。


### Features

* **runtime:** M3 运行时分发 shim——装/升级 Swift 真身并转发命令 ([#80](https://github.com/OrzMC/OrzPythonMC/issues/80)) ([52a7d3c](https://github.com/OrzMC/OrzPythonMC/commit/52a7d3c0d19427da4b1dc258741bae1b12c0787d))

## [2.14.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.13.0...v2.14.0) (2026-10-20)


### Features

* **conformance:** 上报 state.paths（default_state_dir 可注入 + 适配器操作） ([#78](https://github.com/OrzMC/OrzPythonMC/issues/78)) ([4a9f082](https://github.com/OrzMC/OrzPythonMC/commit/4a9f0823265d9daa2591dffd2ae3852d17c60157))


### Bug Fixes

* **app:** 非 macOS 上一律 E_UNSUPPORTED（原来是 E_RUNTIME） ([#77](https://github.com/OrzMC/OrzPythonMC/issues/77)) ([4687d8e](https://github.com/OrzMC/OrzPythonMC/commit/4687d8e563f0484d888bcf36993d220094f21edc))

## [2.13.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.12.0...v2.13.0) (2026-10-19)


### Features

* **m2:** 验收脚本改用父仓库共享实现（删除本地 842 行副本） ([#74](https://github.com/OrzMC/OrzPythonMC/issues/74)) ([e7b8d27](https://github.com/OrzMC/OrzPythonMC/commit/e7b8d27a8f423155dedb33edd81e17237bf60594))

## [2.12.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.11.0...v2.12.0) (2026-10-17)


### ⚠️ 升级须知（补全命令改道 / 脚本需加 --yes）

- **`--install-completion` / `--show-completion` 已移除**。它们在发布二进制里依赖的 shellingham
  动态子模块没有被 PyInstaller 收集，一用就崩（`completion install` 同样受影响）。改用：
  - `orzmc completion install`：按 `$SHELL` 装到标准补全目录（幂等，可重复执行）；
  - `orzmc completion <shell>`：只把脚本打印到 stdout，自行放进 rc。
- **非交互（脚本 / 管道 / CI）下 `remove` / `uninstall` 必须显式 `--yes`**，否则按规范返回取消并退出 **130**。
- 用法错误（未知命令 / 选项 / 缺必填）统一退出 **1**（框架默认的 2 已归一）。
- 错误信息一律写 **stderr**；`--json` 下 stdout 只有信封（`--json` 且不带子命令 → `E_USAGE` + 退出 1）。
- 一键安装器现在装完会**自动安装 Shell 补全**并登记进 `install.conf`（卸载时一并清理）；
  不想装用 `--no-completion` / `ORZMC_NO_COMPLETION=1`。

### Features

* **install:** install.conf 写入 surface（cli|gui，标明哪个前端装了 CLI） ([#69](https://github.com/OrzMC/OrzPythonMC/issues/69)) ([3974436](https://github.com/OrzMC/OrzPythonMC/commit/3974436e5a505e97d2e76228b25c8225447d5733))


### Bug Fixes

* **cli:** 修复冻结二进制补全崩溃并补齐 CLI 自动化验收 ([#71](https://github.com/OrzMC/OrzPythonMC/issues/71)) ([65f24a5](https://github.com/OrzMC/OrzPythonMC/commit/65f24a5b17a7a98b866c112aa1c30201caabd33c))

## [2.11.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.10.0...v2.11.0) (2026-10-17)


### Features

* **cli:** capabilities 上报实际支持的枚举值 ([#67](https://github.com/OrzMC/OrzPythonMC/issues/67)) ([74578c1](https://github.com/OrzMC/OrzPythonMC/commit/74578c175233821a25a67cbe4e175728f17a66e8))

## [2.10.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.9.1...v2.10.0) (2026-10-17)


### ⚠️ 升级须知（卸载命令改名）

「卸载工具自身」的子命令由 `self-uninstall` 改名为 **`uninstall`**（`remove` 仍是移除 Minecraft 版本，语义不变）：

- 旧名 `self-uninstall` **已不存在**，请改用 `orzmc uninstall`（选项不变：`--yes` / `--remove-root` / `--force`）；
- 若脚本 / 文档里写了 `orzmc self-uninstall`，请替换为 `orzmc uninstall`（误用时 CLI 会直接提示 `Did you mean 'uninstall'?`）；
- 规范（`docs/spec/cli-surface.json`）与两套实现（Python / Swift CLI）、一键安装器均已同步。


### Features

* **cli:** 卸载命令 self-uninstall → uninstall（规范先行） ([#66](https://github.com/OrzMC/OrzPythonMC/issues/66)) ([3c061cb](https://github.com/OrzMC/OrzPythonMC/commit/3c061cb3e5a6b069f1cb41509a04bb001c5e571f))


### Bug Fixes

* **cli:** 版本声明改多行、去联系邮箱（由合规单一事实源生成） ([#64](https://github.com/OrzMC/OrzPythonMC/issues/64)) ([b332bfb](https://github.com/OrzMC/OrzPythonMC/commit/b332bfb4e40912477bf906acd2621749db4b71ac))

## [2.9.1](https://github.com/OrzMC/OrzPythonMC/compare/v2.9.0...v2.9.1) (2026-10-16)


### ⚠️ 升级须知（2.9.1 之前的所有版本都必须先按下面方式「过河」）

`2.9.0` 及更早版本存在自升级 bug：以**裸名**（经 `PATH`）执行 `orzmc update` 时，会把**当前工作目录**下的同名文件当成目标 —— 新二进制落在那里，**真正安装的 `orzmc` 不会被替换**（`orzmc version` 仍是旧版），而安装记录 `install.conf` 却已被改写成新版本，看起来“已是最新”。

所以 **`2.9.0` 及更早版本无法用裸名 `orzmc update` 升到本版本**，请任选其一（做一次即可）：

- **用绝对路径执行一次（推荐）**
  - macOS / Linux: `~/.local/bin/orzmc update --yes`
  - Windows（PowerShell）: `& "$env:LOCALAPPDATA\Programs\orzmc\orzmc.exe" update --yes`
- 重跑一键安装器: <https://orzmc.github.io/OrzPythonMC/>
- 从本 Release 下载对应平台资产手动覆盖

升级到 `2.9.1` 后即自带修复，此后再用裸名 `orzmc update` 一切正常。


### Bug Fixes

* orzmc update 别把 CWD 同名文件当目标（裸名 argv[0] 解析） ([#60](https://github.com/OrzMC/OrzPythonMC/issues/60)) ([cec60a8](https://github.com/OrzMC/OrzPythonMC/commit/cec60a8b0f10c8a38b077ca78335579993abbf7c))
* running_binary 在 Windows 优先用 sys.executable（shutil.which 先搜 CWD） ([#62](https://github.com/OrzMC/OrzPythonMC/issues/62)) ([e2bf959](https://github.com/OrzMC/OrzPythonMC/commit/e2bf959e852150411f01b512494e2249586396e6))

## [2.9.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.8.0...v2.9.0) (2026-10-15)


### Features

* **auth:** 内置 Azure 应用 client_id（公共客户端） ([#54](https://github.com/OrzMC/OrzPythonMC/issues/54)) ([ef09e0b](https://github.com/OrzMC/OrzPythonMC/commit/ef09e0b7fa2d5054aa9a66744266356a8402131f))

## [2.8.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.7.0...v2.8.0) (2026-10-15)


### Features

* server --kill-all（进程注册与终止） ([#51](https://github.com/OrzMC/OrzPythonMC/issues/51)) ([de4c213](https://github.com/OrzMC/OrzPythonMC/commit/de4c2137d0c13157b5ed59a6e13cf1a97b4e76c0))

## [2.7.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.6.0...v2.7.0) (2026-10-15)


### Features

* update --channel release|beta ([#49](https://github.com/OrzMC/OrzPythonMC/issues/49)) ([814722f](https://github.com/OrzMC/OrzPythonMC/commit/814722ffbd165e8cf0db6dfa7cc2fc0795ceb5b2))

## [2.6.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.5.0...v2.6.0) (2026-10-15)


### Features

* 服务端 --print-jar-help（打印 jar 帮助） ([#47](https://github.com/OrzMC/OrzPythonMC/issues/47)) ([cfcda47](https://github.com/OrzMC/OrzPythonMC/commit/cfcda4797380ec6fb6c38161bdad5f3d3d00172c))

## [2.5.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.4.1...v2.5.0) (2026-10-15)


### Features

* **cli:** capabilities 命令（机器可读能力清单） ([#41](https://github.com/OrzMC/OrzPythonMC/issues/41)) ([b6886fb](https://github.com/OrzMC/OrzPythonMC/commit/b6886fbb8c95a674b1cb3e883a19e4854f45dcc4))
* **cli:** completion 命令（生成/安装 Shell 补全） ([#40](https://github.com/OrzMC/OrzPythonMC/issues/40)) ([051857a](https://github.com/OrzMC/OrzPythonMC/commit/051857ad9b752d911c75f95acb4b4fb6ca51b105))
* **cli:** 全局 --json 机器可读信封 ([#39](https://github.com/OrzMC/OrzPythonMC/issues/39)) ([c49b1d2](https://github.com/OrzMC/OrzPythonMC/commit/c49b1d2c28be7a14a44dab8d139a7fcbd0c09355))
* **cli:** 全局 --version/--root-dir + 内存选项规范名（保留旧别名） ([#38](https://github.com/OrzMC/OrzPythonMC/issues/38)) ([b4c8dab](https://github.com/OrzMC/OrzPythonMC/commit/b4c8dabcbee5197cd249e9f47271f01241af3bb9))
* **cli:** 新增 plugin 与 app 命令 ([#42](https://github.com/OrzMC/OrzPythonMC/issues/42)) ([11a423a](https://github.com/OrzMC/OrzPythonMC/commit/11a423a7cceb5b23beea8dbd646a871b937ac534))
* 正版授权离线链路 + orzmc mcp server + 服务端选项 ([#46](https://github.com/OrzMC/OrzPythonMC/issues/46)) ([969cd05](https://github.com/OrzMC/OrzPythonMC/commit/969cd051f3de819ab44645b67d4d14a3aa4ecd88))


### Bug Fixes

* **cli:** 用户取消(E_CANCELLED)退出码改为 130(规范 §6) ([#43](https://github.com/OrzMC/OrzPythonMC/issues/43)) ([e4c999a](https://github.com/OrzMC/OrzPythonMC/commit/e4c999a2547a05fb64aac0167220682dc4acc010))
* **infra:** 流式下载中途断连重试并 Range 续传 ([#36](https://github.com/OrzMC/OrzPythonMC/issues/36)) ([50ef96c](https://github.com/OrzMC/OrzPythonMC/commit/50ef96c919b2e0ee54ad474b82fda618ce532e9a))
* **runner:** 服务端退出码归一（信号 → 128+signal） ([#45](https://github.com/OrzMC/OrzPythonMC/issues/45)) ([dee5be7](https://github.com/OrzMC/OrzPythonMC/commit/dee5be7bfdea483ebdb82d381c80c300f9a4e381))

## [2.4.1](https://github.com/OrzMC/OrzPythonMC/compare/v2.4.0...v2.4.1) (2026-09-18)


### Bug Fixes

* **release:** 先建 tag 再跑 release-please,消除假 release PR ([#26](https://github.com/OrzMC/OrzPythonMC/issues/26)) ([db4beee](https://github.com/OrzMC/OrzPythonMC/commit/db4beeed16efffa6f0527b4ca5850d0f1bae15e0))
* **release:** 接力判据改为基于 tag,避免提前建 tag 后漏发版 ([#28](https://github.com/OrzMC/OrzPythonMC/issues/28)) ([93c4b9b](https://github.com/OrzMC/OrzPythonMC/commit/93c4b9b7ea335eec3d42e7287cca5c8540ea80e6))

## [2.4.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.3.0...v2.4.0) (2026-09-18)


### Features

* **release:** PyPI 包附带 PEP 740 产源证明,并给出校验方法 ([#23](https://github.com/OrzMC/OrzPythonMC/issues/23)) ([e132d9c](https://github.com/OrzMC/OrzPythonMC/commit/e132d9c6d00ce04e6e6673d20b59e39008dfda24))

## [2.3.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.2.1...v2.3.0) (2026-09-18)


### Features

* **release:** PyPI 发布切到 OIDC(Trusted Publisher) ([#16](https://github.com/OrzMC/OrzPythonMC/issues/16)) ([e0a55ac](https://github.com/OrzMC/OrzPythonMC/commit/e0a55ac72a0af153dabb7dc09cbef2afed52f9dd))


### Bug Fixes

* **pages:** 发布流水线把 tag 显式传给 Pages(消除注入滞后) ([#17](https://github.com/OrzMC/OrzPythonMC/issues/17)) ([fb3fef8](https://github.com/OrzMC/OrzPythonMC/commit/fb3fef88d56f507e8eeed6cccf093ca4d2fc2604))
* **release:** OIDC 预检并入 release.yml + 发布收尾为 OIDC-only ([#18](https://github.com/OrzMC/OrzPythonMC/issues/18)) ([971b809](https://github.com/OrzMC/OrzPythonMC/commit/971b809bd244ab8943df62a1cdd9e5a0a1604f80))

## [2.2.1](https://github.com/OrzMC/OrzPythonMC/compare/v2.2.0...v2.2.1) (2026-09-18)


### Bug Fixes

* **release:** 所有 checkout 钉死被发布提交 + 二进制自报版本自检(修 2.2.0 二进制版本错误) ([#14](https://github.com/OrzMC/OrzPythonMC/issues/14)) ([4566f76](https://github.com/OrzMC/OrzPythonMC/commit/4566f764224bc764017bf2ab344e89b4069a8426))

## [2.2.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.1.0...v2.2.0) (2026-09-18)


### Features

* **download:** 大文件分块并行(Range),批次 2 = C ([#10](https://github.com/OrzMC/OrzPythonMC/issues/10)) ([d55f63e](https://github.com/OrzMC/OrzPythonMC/commit/d55f63ec7b7dbbc6fa21167b1b76e53dfbeaaa18))
* **download:** 并发可调 + 连接池匹配 + 去掉多余 HEAD(批次 1:A+B+D+E) ([#9](https://github.com/OrzMC/OrzPythonMC/issues/9)) ([0f858f4](https://github.com/OrzMC/OrzPythonMC/commit/0f858f46f8071905171c1d3f5154329afeb2c215))
* **progress:** 进度条显示大小/速度/剩余时间 + 自升级走分块下载 ([#11](https://github.com/OrzMC/OrzPythonMC/issues/11)) ([a9065b0](https://github.com/OrzMC/OrzPythonMC/commit/a9065b08002807197b1825c0324fbdfbfae769c6))


### Bug Fixes

* **release:** release-please job 补 checkout(接力步要读 manifest) ([75bc664](https://github.com/OrzMC/OrzPythonMC/commit/75bc6644d2a773144b1cb13c80d09d48e25203e5))
* **release:** 修正 release-please 的库版本文件路径(库版本号没被 bump) ([#12](https://github.com/OrzMC/OrzPythonMC/issues/12)) ([0717add](https://github.com/OrzMC/OrzPythonMC/commit/0717addb888c60d3315463972c8a855db9a8710c))

## [2.1.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.0.5...v2.1.0) (2026-09-17)


### Features

* 元数据缓存 + 下载进度 + orzmc update 自升级 + 发版流程标准化 ([9ed0dc6](https://github.com/OrzMC/OrzPythonMC/commit/9ed0dc6aa4c7c6e07ae718604e023185bc16e7d5))


### Bug Fixes

* **install+update:** 最新版解析加 302 限流兜底 ([fc8bb02](https://github.com/OrzMC/OrzPythonMC/commit/fc8bb025703942930747a38e3ef89a95e3755184))
