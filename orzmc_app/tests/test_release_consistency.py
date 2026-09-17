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
    assert "GITHUB_REF_NAME" in workflow and "orzmc/version.py" in workflow  # tag == 代码版本
    assert "merge-base --is-ancestor" in workflow  # tag 在 main 上
    assert "--draft=false" in workflow  # 全部成功后才可见


@needs_checkout
def test_version_files_are_wired_into_release_please() -> None:
    """两处版本文件都要带标记,并被 release-please 的 generic updater 覆盖。"""
    for rel in ("orzmc/orzmc/version.py", "orzmc_app/orzmc_app/__init__.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "x-release-please-version" in text, rel
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    assert config["release-type"] == "simple"
    configured = {entry["path"] for entry in config["packages"]["."]["extra-files"]}
    assert configured == {"orzmc/version.py", "orzmc_app/orzmc_app/__init__.py"}
    assert (ROOT / ".release-please-manifest.json").is_file()
