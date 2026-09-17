<!-- 请按 AGENTS.md(事实来源)与 CONTRIBUTING.md 检查后提交 -->

## 这个 PR 做了什么

<!-- 一两句话;关联的 issue 用 "Closes #123" -->

## 变更类型

- [ ] 新功能 `feat`
- [ ] 修 bug `fix`
- [ ] 破坏性变更(提交里写了 `!` 或 `BREAKING CHANGE:`)—— 需要 minor/major 版本
- [ ] 只动文档 / CI / 依赖(`docs` / `ci` / `chore`,不产生新版本)

## 检查清单

- [ ] 提交信息是 Conventional Commits(机器人靠它推版本号与 CHANGELOG)
- [ ] 门禁本地跑过:`ruff check` / `ruff format --check` / `mypy` / `pytest` / `uv build --all-packages`
- [ ] 先库后应用:能力在 `orzmc` 实现 + 测试,再经公共 API 暴露给 `orzmc_app`
- [ ] 没有跨层 import,库内没有 `print` / `os.system`(走 `Reporter` / `ProgressSink`)
- [ ] 涉及架构 / 命令 / 目录 / 流程的改动已同步 `AGENTS.md`(必要时 README / 官网)

## 需要「合前重验收」吗

改动碰到二进制打包、安装器(install.sh / install.ps1)、自升级、PATH/编码、平台差异时,**勾上并贴出结果**:

```bash
gh workflow run ci.yml --ref <这个分支> -f full=true   # 6 平台 binary + installer
```

- [ ] 不需要(纯库逻辑 / 文档)
- [ ] 已跑 `full=true`,结果:<run 链接>
