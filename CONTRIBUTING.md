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
   - `pypi`:两个包发布到 PyPI —— **优先 OIDC(Trusted Publisher)**,失败才回退 API token(见下);
   - `publish`:全部成功后才把 Release 从 draft 变可见 —— 半成品(有二进制、没 PyPI 包)不会出现在 `releases/latest`,官网与一键安装也不会提前推新版本。
4. 官网(`pages.yml`)把「最新版」指向新版本 —— 注意这一步是 `release.yml` **显式 dispatch** 的:GITHUB_TOKEN 产生的 tag/release 事件不会触发工作流,所以整条链路都靠显式接力(见 `AGENTS.md`)。

紧急情况下有两种手动路径:`git tag vX.Y.Z && git push origin vX.Y.Z`(tag push 会正常触发流水线),或补发一次已有 tag:

```bash
gh workflow run release.yml --ref main -f tag=vX.Y.Z
```

两条路都走同一套护栏(`verify`:tag == 代码版本、tag 在 main 上)与原子发布(draft → 二进制 → PyPI → 可见)。

### 版本策略:预稳定期破坏性变更暂走 minor

当前处于 2.x 预稳定期(API 仍在收敛、用户面很小),**破坏性 API 变更暂不使用 `feat!` / `BREAKING CHANGE:`**(那会让 release-please 直接推 major),而是走 minor 并在 CHANGELOG 里人工注明影响;等准备把 API 冻结成 3.0.0 时再启用 `!`。这条是刻意选择,不是疏忽。

### 需要人工配置/授权的一次性事项

- `RELEASE_PLEASE_TOKEN`(可选):细粒度 PAT 存到 repo secret,让机器人开的 release PR 也触发 CI;不配则用默认 `GITHUB_TOKEN`(release PR 上无检查,风险低)。
- PyPI **Trusted Publishing**(推荐,替代长期 token):**只需在 PyPI 后台点几下**,CI 侧已就绪(优先 OIDC、失败回退 token)。
  1. 两个项目各配一次,进入 <https://pypi.org/manage/project/orzmc/settings/publishing/> 与 <https://pypi.org/manage/project/orzmc-app/settings/publishing/>,点 **Add a new publisher** → **GitHub**,填:
     - Owner:`OrzMC`;Repository name:`OrzPythonMC`;Workflow name:**`release.yml`**(必须与工作流文件名完全一致);Environment name:**`release`**(必须与 `release.yml` 里 `pypi` job 的 `environment` 一致)。
     - **三个字段填错都不会在配置时报错**,只会在下次发版/预检时失败 —— 所以配完立刻做第 2 步验证。
  2. 立刻预检(**不用等下次发版**):`gh workflow run pypi-oidc-check.yml --ref main`,然后 `gh run watch`。它用 `uv publish --dry-run --trusted-publishing always` 真的去换一次 OIDC token,两个包都打印 `OK: … 的 OIDC 交换成功` 才算配好。
  3. 之后下一次发版看 `pypi` job 日志:出现 **`已通过 OIDC(Trusted Publisher)发布 …`** 就说明真的走 OIDC 了(若是 `::warning:: … 回退到 API token` 则说明还没生效)。
  4. 确认走通后再收尾:删除两个 token secret(`gh secret delete PYPI_API_TOKEN_ORZMC_LIB PYPI_API_TOKEN_ORZMC_APP`),并清掉 `release.yml` 里两个 `UV_PUBLISH_TOKEN` env 与 `scripts/publish_idempotent.sh` 的 token 回退分支。
- 分支保护:`main` 要求 `quality` 与 `test (ubuntu-latest, x86_64)` 通过、squash-only、admin 可绕过。

## 不要做的事

- 不要手改 `CHANGELOG.md`、`orzmc/version.py`、`orzmc_app/orzmc_app/__init__.py` 的版本(release PR 会覆盖;`x-release-please-version` 注释不要删)。
- 不要把「版本提升」当成独立提交混进功能 PR(交给机器人)。
- 不要为了做验收去改 `ci.yml` 的 `on.push.branches`(用 `-f full=true`)。
- 不要在库里 `print` / `os.system`(走 `Reporter`/`ProgressSink`),不要跨层 import(见 `AGENTS.md`)。
