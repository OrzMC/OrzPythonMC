# Changelog

## [2.5.0](https://github.com/OrzMC/OrzPythonMC/compare/v2.4.0...v2.5.0) (2026-09-18)


### Features

* **acceptance:** 上游平台空缺记 UP(gap),不再判 FAIL ([1b81104](https://github.com/OrzMC/OrzPythonMC/commit/1b81104b37beb76f02ad8e8f09eb1ec0d69cc398))
* **client:** 玩家名交互提示 + 文档示例 ([53d7f6b](https://github.com/OrzMC/OrzPythonMC/commit/53d7f6b263ac8f628aa99daff8cff11346175532))
* **download:** 大文件分块并行(Range),批次 2 = C ([#10](https://github.com/OrzMC/OrzPythonMC/issues/10)) ([d55f63e](https://github.com/OrzMC/OrzPythonMC/commit/d55f63ec7b7dbbc6fa21167b1b76e53dfbeaaa18))
* **download:** 并发可调 + 连接池匹配 + 去掉多余 HEAD(批次 1:A+B+D+E) ([#9](https://github.com/OrzMC/OrzPythonMC/issues/9)) ([0f858f4](https://github.com/OrzMC/OrzPythonMC/commit/0f858f46f8071905171c1d3f5154329afeb2c215))
* **picker:** 交互式版本选择器——版本目录 API + 全屏键盘 TUI ([e5be5e5](https://github.com/OrzMC/OrzPythonMC/commit/e5be5e52347c2064fc96bd66e07578eeaf7b9a84))
* **progress:** 进度条显示大小/速度/剩余时间 + 自升级走分块下载 ([#11](https://github.com/OrzMC/OrzPythonMC/issues/11)) ([a9065b0](https://github.com/OrzMC/OrzPythonMC/commit/a9065b08002807197b1825c0324fbdfbfae769c6))
* **release:** PyPI 包附带 PEP 740 产源证明,并给出校验方法 ([#23](https://github.com/OrzMC/OrzPythonMC/issues/23)) ([e132d9c](https://github.com/OrzMC/OrzPythonMC/commit/e132d9c6d00ce04e6e6673d20b59e39008dfda24))
* **release:** PyPI 发布切到 OIDC(Trusted Publisher) ([#16](https://github.com/OrzMC/OrzPythonMC/issues/16)) ([e0a55ac](https://github.com/OrzMC/OrzPythonMC/commit/e0a55ac72a0af153dabb7dc09cbef2afed52f9dd))
* **server:** 新增 --nogui 选项 + Ctrl-C 优雅回收服务端 ([0462a2b](https://github.com/OrzMC/OrzPythonMC/commit/0462a2be46630446c6bad8048c3c5b602eb9befe))
* **site:** 静态官网 + GitHub Pages 自动部署 ([b37ac19](https://github.com/OrzMC/OrzPythonMC/commit/b37ac19cc4943e8f4631d3f2cbf0d686eb3c9845))
* 元数据缓存 + 下载进度 + orzmc update 自升级 + 发版流程标准化 ([9ed0dc6](https://github.com/OrzMC/OrzPythonMC/commit/9ed0dc6aa4c7c6e07ae718604e023185bc16e7d5))
* 游戏类型重构 — 客户端 vanilla/fabric/forge + 服务端 vanilla/paper/fabric/forge ([b48130e](https://github.com/OrzMC/OrzPythonMC/commit/b48130e6ee8c36df877b20a8f788b3131c98fd98))
* 跨平台 CI(6 平台矩阵)+ 真实验收 harness ([a1ca98d](https://github.com/OrzMC/OrzPythonMC/commit/a1ca98d777e337bdff1a2c140af149282a86d2c1))
* 通用一键安装/卸载(两层安装器)+ CI e2e ([6b0d522](https://github.com/OrzMC/OrzPythonMC/commit/6b0d52244af74b6e24f09b422eb5c1aad86542cd))
* 重构为 app+库 / uv workspace / Textual TUI (v2.0) ([b855a1a](https://github.com/OrzMC/OrzPythonMC/commit/b855a1a98418660701f6f6b5f7036ad4c5cfbc31))


### Bug Fixes

* **acceptance:** fabric 客户端 natives 缺口补崩溃报告帧签名 ([686d91c](https://github.com/OrzMC/OrzPythonMC/commit/686d91c4c8f13a5631ef8fcea20958ff8f1545d8))
* **acceptance:** UP(gap) 匹配折行的日志 + 补 LWJGL 加载失败签名 ([ab9c0bb](https://github.com/OrzMC/OrzPythonMC/commit/ab9c0bb3877f0c816b37d91a15cea82a9bc318da))
* **acceptance:** Windows UTF-8 输出崩溃 + 失败时打印日志尾部/上传日志 ([74f3a4c](https://github.com/OrzMC/OrzPythonMC/commit/74f3a4c9feefcafe31acea21b9c35f0c1452fc7d))
* **ci:** 修复 classpath native 测试的 OS 依赖 + 加固 test job 的 inputs 表达式 ([b12b599](https://github.com/OrzMC/OrzPythonMC/commit/b12b599e5c29bffcd56860fe09184426b1a42350))
* **install+update:** 最新版解析加 302 限流兜底 ([fc8bb02](https://github.com/OrzMC/OrzPythonMC/commit/fc8bb025703942930747a38e3ef89a95e3755184))
* **install:** PS5.1 一键安装中文自愈 + 剥 BOM + emoji 换 [OK] ([d4e049b](https://github.com/OrzMC/OrzPythonMC/commit/d4e049b89269f2f427aa9fdd9da9c3d5330fb94a))
* **pages:** 发布流水线把 tag 显式传给 Pages(消除注入滞后) ([#17](https://github.com/OrzMC/OrzPythonMC/issues/17)) ([fb3fef8](https://github.com/OrzMC/OrzPythonMC/commit/fb3fef88d56f507e8eeed6cccf093ca4d2fc2604))
* **release:** build.py 支持 --name,6 平台二进制不再互相覆盖 ([3fa67a1](https://github.com/OrzMC/OrzPythonMC/commit/3fa67a1f5228a238852e105e0551670bf85c96e5))
* **release:** OIDC 预检并入 release.yml + 发布收尾为 OIDC-only ([#18](https://github.com/OrzMC/OrzPythonMC/issues/18)) ([971b809](https://github.com/OrzMC/OrzPythonMC/commit/971b809bd244ab8943df62a1cdd9e5a0a1604f80))
* **release:** release-please job 补 checkout(接力步要读 manifest) ([75bc664](https://github.com/OrzMC/OrzPythonMC/commit/75bc6644d2a773144b1cb13c80d09d48e25203e5))
* **release:** 修正 release-please 的库版本文件路径(库版本号没被 bump) ([#12](https://github.com/OrzMC/OrzPythonMC/issues/12)) ([0717add](https://github.com/OrzMC/OrzPythonMC/commit/0717addb888c60d3315463972c8a855db9a8710c))
* **release:** 所有 checkout 钉死被发布提交 + 二进制自报版本自检(修 2.2.0 二进制版本错误) ([#14](https://github.com/OrzMC/OrzPythonMC/issues/14)) ([4566f76](https://github.com/OrzMC/OrzPythonMC/commit/4566f764224bc764017bf2ab344e89b4069a8426))
* **runner:** JVM 安装器 GBK 输出不再中断 fabric/forge 部署 ([2653e16](https://github.com/OrzMC/OrzPythonMC/commit/2653e169a5693dd89b7a1a4be7ab2f7e7501d8df))
* **tests:** Windows 上 java 可执行名为 java.exe(java_bin 加后缀) ([e82e596](https://github.com/OrzMC/OrzPythonMC/commit/e82e596e296415f6dc0396864a6fc2d6de9caf2c))
* **tests:** 修复 Windows 上的路径分隔符断言与 JavaEnv zip 罐头 ([3839d76](https://github.com/OrzMC/OrzPythonMC/commit/3839d76cc3ff7ae083de515cf5b332c68d9be836))
* Windows CI 失败(rc 还原路由 + install.ps1 编码) ([e9849a8](https://github.com/OrzMC/OrzPythonMC/commit/e9849a8e0027f07b9dd41f6a9ae59dd91d2a3d62))
* Windows 管道输出中文 UnicodeEncodeError — 强制 UTF-8 stdio ([9994396](https://github.com/OrzMC/OrzPythonMC/commit/999439631a6a398f710901a62ab7b6de1ff0ab89))
* 交互版本选择器 NameError — 惰性名须经模块属性访问 ([c2b97e6](https://github.com/OrzMC/OrzPythonMC/commit/c2b97e6b73547cedd0c77c6e70879a1dfc423d2f))
* 真实验收修复 Paper/Fabric API 变更与 Forge 安装缓存 ([c4dd886](https://github.com/OrzMC/OrzPythonMC/commit/c4dd886a967a7cd85315148725fd7aed2c1bf353))


### Performance Improvements

* frozen 启动 14s→1.4s — 强制 uv standalone Python + 懒加载 ([56017de](https://github.com/OrzMC/OrzPythonMC/commit/56017dea54c1b8b4b4b340262ac1e8f07f336794))

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
