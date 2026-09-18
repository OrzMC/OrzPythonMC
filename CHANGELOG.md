# Changelog

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
