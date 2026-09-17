# 贡献指南(OrzMC)

本仓库的**事实来源是 [`AGENTS.md`](AGENTS.md)**:架构分层、目录结构、命令、发布流程都以它为准,动手前先读。本文件只讲「怎么把一个改动合进来、怎么发版」。

## 日常迭代:trunk-based

- 只有一条长期分支 `main`,永远保持可发版状态;干活开短命分支(`feat/xxx`、`fix/xxx`、`docs/xxx`),合完即删。
- **提交信息用 Conventional Commits**(`feat:` / `fix:` / `docs:` / `chore:` / `perf:` / `test:` / `ci:` / `refactor:`,可带 scope 如 `fix(update):`)。机器人据此推导版本号与 CHANGELOG,写错就等于发版出错。
  - 破坏性改动用 `feat!:` 或正文写 `BREAKING CHANGE:`。
- 用 PR 合并,合并方式固定 **squash**;`main` 有分支保护,必需的检查必须绿。

## 本地门禁(提交前自己跑)

```bash
uv sync --all-packages
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run --all-packages pytest
uv build --all-packages
```

改动涉及安装器 / 自升级时,再跑一遍端到端验收(纯 stdlib、跨平台、完全隔离,不碰真实 PATH/rc/游戏目录):

```bash
uv run python scripts/accept_selfupdate.py                # 构建 + 安装 + 更新 + 卸载全流程
uv run python scripts/accept_selfupdate.py --skip-build   # 复用已有二进制,快
```

## PR 上的 CI 与「合前重验收」

PR 默认只跑轻量检查(`quality` + 6 平台 pytest),因为 6 平台 PyInstaller 构建 + 安装器 e2e 很贵。需要它们时**不要改工作流的触发条件**,用正规入口按需触发:

```bash
gh workflow run ci.yml --ref <你的分支> -f full=true     # 6 平台 binary + installer
gh run watch                                             # 跟进结果
```

改动踩到二进制打包、安装器、PATH/编码、平台差异时,合前请务必跑一次 `full`。合进 `main` 后这些重活也会自动再跑一遍。

## 发版:release-please 开 PR,人按按钮

版本号**不要手改**。流程是:

1. 功能 PR 合进 `main` 后,`release-please` 会根据 Conventional Commits 自动开一个 **release PR**(标题形如 `chore(main): release 2.1.0`),里面是:
   - `orzmc/version.py` 与 `orzmc_app/orzmc_app/__init__.py` 的版本号(两处锁步,有单测守着);
   - `CHANGELOG.md` 的新条目;
   - `.release-please-manifest.json` 的更新。
2. **review 这个 PR**(重点看 CHANGELOG 措辞与版本号是否合理),合并它 = 按下发版按钮。
3. 合并后机器人自动打 `vX.Y.Z` tag 并创建带 notes 的 GitHub Release,接着 `release.yml`:
   - `verify`:断言 **tag == `orzmc/version.py` 里的版本**、且 tag 指向 `main` 上的提交(防「版本没改就发版」——那会让客户端自升级陷死循环);
   - `quality`:单 runner 质量门禁;
   - `binary`:6 平台 PyInstaller 产物挂到 Release;
   - `pypi`:两个包分别用各自 scope 的 token 发布到 PyPI;
   - `publish`:全部成功后才把 Release 从 draft 变可见 —— 半成品(有二进制、没 PyPI 包)不会出现在 `releases/latest`,官网与一键安装也不会提前推新版本。
4. 官网(`pages.yml`)会自动把「最新版」指向新版本。

紧急情况下手动打 tag 也可以:`git tag vX.Y.Z && git push origin vX.Y.Z` —— `verify` 会挡住版本不一致,Release 会自动以 draft 建好并在二进制 + PyPI 都成功后发布。

### 需要人工配置/授权的一次性事项

- `RELEASE_PLEASE_TOKEN`(可选):细粒度 PAT 存到 repo secret,让机器人开的 release PR 也触发 CI;不配则用默认 `GITHUB_TOKEN`(release PR 上无检查,风险低)。
- PyPI **Trusted Publishing**(可选,替代长期 token):两个包在 PyPI 后台各配 trusted publisher(repo `OrzMC/OrzPythonMC`、workflow `release.yml`、environment `release`),CI 侧加 `permissions: id-token: write` 并把 `UV_PUBLISH_TOKEN` 换成 `uv publish --trusted-publishing always`。
- 分支保护:`main` 要求 `quality` 与 `test (ubuntu-latest, x86_64)` 通过、squash-only、admin 可绕过。

## 不要做的事

- 不要手改 `CHANGELOG.md`、`orzmc/version.py`、`orzmc_app/orzmc_app/__init__.py` 的版本(release PR 会覆盖;`x-release-please-version` 注释不要删)。
- 不要把「版本提升」当成独立提交混进功能 PR(交给机器人)。
- 不要为了做验收去改 `ci.yml` 的 `on.push.branches`(用 `-f full=true`)。
- 不要在库里 `print` / `os.system`(走 `Reporter`/`ProgressSink`),不要跨层 import(见 `AGENTS.md`)。
