"""发布链路一致性守卫:官网 / 库 updater / release.yml / release-please 不许漂移。

这些字符串一旦对不上,后果都是用户可见的:官网下载按钮 404、自升级找不到资产、
release-please 悄悄不再 bump 版本号、发版少了「tag == 代码版本」的护栏。写进测试比
写在文档里可靠(文档会过期,回归不会)。

测试直接读仓库文件,所以只在源码检出里有效(安装后的 wheel 不带 tests/);
库内部模块 `orzmc.services.selfupdate` 在这里被引用是刻意的 —— 资产命名规则的
**唯一定义处**就在那里,守卫必须与它比对而不是复制一份。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from orzmc.services.selfupdate import DOWNLOAD_BASE, asset_for

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "docs" / "index.html"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
TAG_PLACEHOLDER = "__ORZMC_LATEST_TAG__"

#: ``asset_for`` 接受的 system 名(Python 口径)+ 两种架构。
PLATFORM_COMBOS = [
    ("darwin", "x86_64"),
    ("darwin", "arm64"),
    ("linux", "x86_64"),
    ("linux", "arm64"),
    ("windows", "x86_64"),
    ("windows", "arm64"),
]

needs_checkout = pytest.mark.skipif(not SITE.is_file(), reason="仅在仓库检出内可用")


def _site_names() -> set[str]:
    return set(re.findall(r'file: "([^"]+)"', SITE.read_text(encoding="utf-8")))


@needs_checkout
def test_site_download_names_match_the_updater() -> None:
    """官网列出的资产名必须与库侧 asset_for() 一模一样(Windows 带 .exe)。"""
    expected = {asset_for(system, machine)[0] for system, machine in PLATFORM_COMBOS}
    assert _site_names() == expected


@needs_checkout
def test_release_workflow_builds_exactly_the_site_assets() -> None:
    """release.yml 的矩阵产物名 == 官网资产名去掉 .exe(PyInstaller 在 Windows 落盘时加)。"""
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    artifacts = set(re.findall(r"artifact: (\S+)", workflow))
    assert artifacts == {name.removesuffix(".exe") for name in _site_names()}


@needs_checkout
def test_site_download_base_matches_the_updater() -> None:
    """官网拼下载地址的仓库与基线必须与库一致(库按 DOWNLOAD_BASE/<tag>/<asset> 自升级)。"""
    site = SITE.read_text(encoding="utf-8")
    repo = DOWNLOAD_BASE.split("github.com/", 1)[1].split("/releases/download", 1)[0]
    assert f'var REPO = "{repo}";' in site
    assert 'var DOWNLOAD_BASE = "https://github.com/" + REPO + "/releases/download";' in site


@needs_checkout
def test_pages_deploy_injects_the_latest_tag() -> None:
    """官网"最新版"由部署期注入(不是浏览器调 API),占位符与注入步必须成对存在。"""
    site = SITE.read_text(encoding="utf-8")
    pages = PAGES_WORKFLOW.read_text(encoding="utf-8")
    assert f'content="{TAG_PLACEHOLDER}"' in site
    assert TAG_PLACEHOLDER in pages
    # 发版后要重新部署,否则页面会停在旧版本。
    assert "types: [published]" in pages
    step = pages.split("Inject the latest release tag", 1)[1].split("Upload site", 1)[0]
    assert "-w '%{redirect_url}'" in step
    # 跟随重定向后 %{redirect_url} 是空串 —— install.sh 里踩过同一个坑。
    # (只断言 curl 的实参,注释里提到 -L 无妨。)
    assert "curl -sS" in step
    assert "curl -fsSL" not in step and "curl -sSL" not in step


@needs_checkout
def test_release_guardrails_are_present() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    # tag == 代码版本(手动 dispatch 与 tag push 走同一段判断,统一用 $TAG)
    assert "orzmc/version.py" in workflow
    assert 'if [ "$TAG" != "v$version" ]; then' in workflow
    assert "merge-base --is-ancestor" in workflow  # tag 在 main 上
    assert "--draft=false" in workflow  # 全部成功后才可见


@needs_checkout
def test_release_please_hands_off_to_the_release_pipeline() -> None:
    """GITHUB_TOKEN 建的 tag 不触发工作流 → release-please 必须显式接力 dispatch。

    这条一旦丢掉,发版会静默停摆:tag 与 Release 都在,却没有二进制、没发 PyPI。
    """
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    please = (ROOT / ".github" / "workflows" / "release-please.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release and "tag:" in release
    assert 'gh workflow run release.yml --ref "$GITHUB_DEFAULT_BRANCH" -f tag="$tag"' in please
    assert "actions: write" in please
    # release 可见性交给 release.yml 的收尾(publish),所以由 release-please 建 draft。
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    assert config["draft"] is True
    # 官网"最新版"是部署期注入的,发布完还要显式重部署 Pages。
    assert "gh workflow run pages.yml" in release
    # binary job 会跑在 Windows 上,shell 是 pwsh:那里 `$TAG` 是 PowerShell 变量(空),
    # 只有 `$env:TAG` 是环境变量 —— 跨平台步骤必须内联表达式。
    assert 'gh release upload "$TAG"' not in release
    assert 'gh release upload "${{ inputs.tag || github.ref_name }}"' in release
    # publish job 用 gh workflow run,而 job 级 permissions 会覆盖工作流级 —— 必须自带
    # actions: write,否则 403 Resource not accessible by integration。
    publish_job = release.split("\n  publish:", 1)[1]
    assert "actions: write" in publish_job
    # PyPI 不允许覆盖同名版本:重跑发布流水线必须幂等。
    assert "./scripts/publish_idempotent.sh" in release
    assert (ROOT / "scripts" / "publish_idempotent.sh").is_file()


@needs_checkout
def test_version_files_are_wired_into_release_please() -> None:
    """两处版本文件都要带标记,并被 release-please 的 generic updater 覆盖。"""
    for rel in ("orzmc/orzmc/version.py", "orzmc_app/orzmc_app/__init__.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "x-release-please-version" in text, rel
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    assert config["release-type"] == "simple"
    configured = {entry["path"] for entry in config["packages"]["."]["extra-files"]}
    # 路径必须真实存在:写错路径 release-please 会**静默跳过**,于是库版本号不跟着
    # bump,而 tag 与版本号不一致要到 release.yml 的 verify 才红。曾经真踩过:
    # 配置里写的是 orzmc/version.py,文件其实在 orzmc/orzmc/version.py。
    for rel in configured:
        assert (ROOT / rel).is_file(), f"release-please extra-files 路径不存在: {rel}"
    # 反向也要成立:带标记的版本文件一个都不能漏(用扫描而不是写死列表,否则守卫会
    # 变成「和实现对账」而不是「和不变式对账」)。
    marked: set[str] = set()
    for package in ("orzmc/orzmc", "orzmc_app/orzmc_app"):
        for path in (ROOT / package).rglob("*.py"):
            if "x-release-please-version" in path.read_text(encoding="utf-8"):
                marked.add(str(path.relative_to(ROOT)).replace(os.sep, "/"))
    assert marked == configured
    assert (ROOT / ".release-please-manifest.json").is_file()
