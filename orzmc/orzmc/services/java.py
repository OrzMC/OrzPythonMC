"""Sandboxed Java runtime: resolve or auto-install a Temurin JRE/JDK.

The runtime lives under ``<root>/java/<major>/`` — no dependency on a system
Java installation.
"""

from __future__ import annotations

import os
import platform
import shutil
import tarfile
import zipfile
from collections.abc import Callable

from orzmc.domain.paths import PathLayout
from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import Reporter
from orzmc.infra.progress import ProgressSink
from orzmc.infra.transfer import download_with_progress

ADOPTIUM_BINARY = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/{image}/hotspot/normal/eclipse"


class JavaEnv:
    """Resolve (installing on demand) a sandboxed Java runtime."""

    def __init__(
        self,
        http: HttpClient,
        fs: FileStore,
        reporter: Reporter,
        sink: ProgressSink,
        paths: PathLayout,
    ) -> None:
        self._http = http
        self._fs = fs
        self._reporter = reporter
        self._sink = sink
        self._paths = paths

    def resolve(
        self,
        major: int,
        need_jdk: bool = False,
        confirm: Callable[[int, bool], bool] | None = None,
    ) -> str:
        """Return the managed java executable, installing a runtime when missing.

        ``confirm`` (optional) is asked before any install; returning False aborts.
        """
        java_bin = self._paths.java_bin(major)
        if self._fs.is_file(java_bin):
            self._reporter.debug(f"使用已安装的 Java {major}: {java_bin}")
            return java_bin
        if confirm is not None and not confirm(major, need_jdk):
            raise RuntimeError(f"未安装 Java {major},已取消操作")
        self._install(major, need_jdk)
        if not self._fs.is_file(java_bin):
            raise RuntimeError(f"Java {major} 安装失败:{java_bin}")
        return java_bin

    # ── install ─────────────────────────────────────────────────────────────

    def _install(self, major: int, need_jdk: bool) -> None:
        image = "jdk" if need_jdk else "jre"
        os_name, arch = self._adoptium_os_arch()
        url = ADOPTIUM_BINARY.format(major=major, os=os_name, arch=arch, image=image)
        desc = f"下载 Temurin {image.upper()} {major} ({os_name}/{arch})"
        self._reporter.info(desc)
        tmp_dir = self._paths.download_tmp_dir()
        self._fs.ensure_dir(tmp_dir)
        ext = ".zip" if os_name == "windows" else ".tar.gz"
        archive = os.path.join(tmp_dir, f"temurin-{major}-{image}-{os_name}-{arch}{ext}")

        download_with_progress(self._http, url, archive, self._sink, desc)

        dest_dir = self._paths.java_major_dir(major)
        self._fs.ensure_dir(dest_dir)
        self._extract(archive, dest_dir)
        self._fs.remove(archive)
        self._reporter.success(f"Java {major} ({image}) 已安装到 {dest_dir}")

    def _extract(self, archive: str, dest_dir: str) -> None:
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest_dir)
        else:
            with tarfile.open(archive, "r:*") as tf:
                try:
                    tf.extractall(dest_dir, filter="data")
                except TypeError:  # Python < 3.12
                    tf.extractall(dest_dir)
        # Adoptium archives contain a single top-level dir; flatten it.
        children = [
            name
            for name in os.listdir(dest_dir)
            if os.path.isdir(os.path.join(dest_dir, name)) and not name.startswith(".")
        ]
        if len(children) == 1:
            top = os.path.join(dest_dir, children[0])
            for name in os.listdir(top):
                shutil.move(os.path.join(top, name), dest_dir)
            shutil.rmtree(top, ignore_errors=True)
        # macOS bundles the JRE as <name>.jre/Contents/Home/bin/... ; hoist Home.
        home = os.path.join(dest_dir, "Contents", "Home")
        if os.path.isdir(home):
            for name in os.listdir(home):
                shutil.move(os.path.join(home, name), dest_dir)
            shutil.rmtree(os.path.join(dest_dir, "Contents"), ignore_errors=True)

    @staticmethod
    def _adoptium_os_arch() -> tuple[str, str]:
        system = platform.system().lower()
        if system == "darwin":
            os_name = "mac"
        elif system == "windows":
            os_name = "windows"
        else:
            os_name = "linux"
        machine = platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            arch = "aarch64"
        elif machine in ("x86_64", "amd64"):
            arch = "x64"
        else:
            arch = machine or "x64"
        return os_name, arch
