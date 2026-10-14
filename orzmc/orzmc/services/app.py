"""Cross-face GUI app (``OrzMCApp``) install for macOS.

Backs ``orzmc app install|open|uninstall|status`` (spec
``docs/spec/install-update.md`` §8). Only macOS has a GUI build today; other
platforms explicitly report "unsupported" rather than failing silently.

The ``release`` channel downloads the newest ``.zip`` asset from the app's
GitHub Release (assets are timestamped, so the newest by name wins), extracts
the ``.app`` and installs it under ``~/Applications`` (user-level, no sudo).
"""

from __future__ import annotations

import os
import plistlib
import sys
import tempfile
from dataclasses import dataclass

from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.runner import ProcessRunner

APP_REPO = "OrzMC/OrzMCApp"
APP_NAME = "OrzMC.app"
API_LATEST = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{APP_REPO}/releases/tags"
#: App Store 上架后填写;目前 ``appstore`` 渠道显式不支持。
APPSTORE_URL = "macappstore://apps.apple.com/app/id0000000000"


@dataclass(frozen=True)
class AppStatus:
    """Installed GUI app state."""

    installed: bool
    path: str
    version: str | None


class AppInstaller:
    """Install / open / uninstall the macOS GUI app."""

    def __init__(
        self,
        http: HttpClient | None = None,
        fs: FileStore | None = None,
        process: ProcessRunner | None = None,
        reporter: Reporter | None = None,
    ) -> None:
        self._http = http or HttpClient()
        self._fs = fs or FileStore()
        self._process = process or ProcessRunner(reporter)
        self._reporter = reporter or NullReporter()

    @property
    def available(self) -> bool:
        """Whether this platform has a GUI build (macOS only today)."""
        return sys.platform == "darwin"

    @property
    def installed_path(self) -> str:
        return os.path.join(self.applications_dir(), APP_NAME)

    def applications_dir(self) -> str:
        """User-level applications dir (``~/Applications``; no sudo)."""
        return os.path.join(os.path.expanduser("~"), "Applications")

    def _require_macos(self) -> None:
        if not self.available:
            raise RuntimeError("GUI 应用仅支持 macOS;其它平台计划中")

    def status(self) -> AppStatus:
        """Installed / not-installed plus the app version from its Info.plist."""
        path = self.installed_path
        if not self._fs.exists(path):
            return AppStatus(installed=False, path=path, version=None)
        return AppStatus(installed=True, path=path, version=self._installed_version())

    def _installed_version(self) -> str | None:
        plist_path = os.path.join(self.installed_path, "Contents", "Info.plist")
        if not self._fs.is_file(plist_path):
            return None
        try:
            with open(plist_path, "rb") as handle:
                data = plistlib.load(handle)
        except Exception:
            return None
        version = data.get("CFBundleShortVersionString")
        return str(version) if version else None

    def resolve(self, version: str | None = None) -> tuple[str, str]:
        """``(tag, zip_url)`` for the given release tag or the latest release."""
        url = f"{API_TAG}/{version}" if version else API_LATEST
        data = self._http.get_json(url, headers={"Accept": "application/vnd.github+json"})
        if not isinstance(data, dict):
            raise RuntimeError("无法解析应用发布信息")
        tag = str(data.get("tag_name") or "")
        assets = data.get("assets") or []
        urls = [str(a.get("browser_download_url")) for a in assets if isinstance(a, dict)]
        zips = sorted(u for u in urls if u.lower().endswith(".zip"))
        if not tag or not zips:
            raise RuntimeError(f"发布 {tag or '?'} 没有 .zip 产物")
        return tag, zips[-1]

    def install(self, version: str | None = None) -> str:
        """Download, extract and install the app; returns the installed path."""
        self._require_macos()
        tag, url = self.resolve(version)
        workspace = tempfile.mkdtemp(prefix="orzmc-app-")
        try:
            zip_path = os.path.join(workspace, "app.zip")
            self._reporter.info(f"下载 OrzMC {tag}")
            self._http.download(url, zip_path)
            extract_dir = os.path.join(workspace, "extract")
            self._fs.ensure_dir(extract_dir)
            self._process.run_stream(["/usr/bin/ditto", "-x", "-k", zip_path, extract_dir])
            app_path = _find_app(extract_dir)
            if app_path is None:
                raise RuntimeError("解压后未找到 .app")
            self._fs.ensure_dir(self.applications_dir())
            destination = self.installed_path
            self._fs.remove(destination)
            self._fs.move(app_path, destination)
            return destination
        finally:
            self._fs.remove(workspace)

    def open(self) -> None:
        """Open the installed app (macOS ``open``)."""
        self._require_macos()
        if not self._fs.exists(self.installed_path):
            raise RuntimeError("未安装 OrzMC 应用;先运行 orzmc app install")
        self._process.run_stream(["/usr/bin/open", self.installed_path])

    def uninstall(self) -> bool:
        """Remove the installed app; True when something was removed."""
        self._require_macos()
        if not self._fs.exists(self.installed_path):
            return False
        self._fs.remove(self.installed_path)
        return True


def _find_app(directory: str) -> str | None:
    for name in sorted(os.listdir(directory)):
        if name.endswith(".app"):
            return os.path.join(directory, name)
    return None
