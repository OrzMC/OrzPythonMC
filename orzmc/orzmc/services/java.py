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
from orzmc.infra.http import HttpClient, HttpStatusError
from orzmc.infra.log import Reporter
from orzmc.infra.progress import ProgressSink
from orzmc.infra.transfer import PARALLEL_PARTS, download_with_progress

ADOPTIUM_BINARY = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/{image}/hotspot/normal/eclipse"


def _install_failure(exc: HttpStatusError, *, major: int, image: str, os_name: str, arch: str) -> str:
    """把 Adoptium 的 HTTP 错误转成用户能执行的提示。

    Temurin 并非每个 OS/arch 都有每个 Java 大版本(实测 Java 25 没有 windows/aarch64
    的 jre 与 jdk,Java 21 有)—— 那是上游缺包,不是网络问题。原文保留在末尾:
    验收 harness 靠 `404 Client Error: Not Found for url: https://api.adoptium.net/…`
    把这类平台缺口归为 UP(gap)(见 `scripts/acceptance.py` 的 GAP_MARKS)。
    """
    if exc.status == 404:
        return (
            f"Temurin 没有 Java {major} 的 {os_name}/{arch} 构建(上游未发布,HTTP 404)。"
            f" 可自行安装 Java {major} 并加入 PATH,或改用其它平台/架构。原始错误: {exc}"
        )
    return f"下载 Java {major} {image.upper()} 失败(HTTP {exc.status}): {exc}"


class JavaEnv:
    """Resolve (installing on demand) a sandboxed Java runtime."""

    def __init__(
        self,
        http: HttpClient,
        fs: FileStore,
        reporter: Reporter,
        sink: ProgressSink,
        paths: PathLayout,
        parts: int = PARALLEL_PARTS,
    ) -> None:
        self._http = http
        self._fs = fs
        self._reporter = reporter
        self._sink = sink
        self._paths = paths
        # JRE 归档 40MB+ —— 单流在高延迟/丢包链路上明显更慢(实测约 1.6 倍差距)。
        self._parts = parts

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

        try:
            download_with_progress(self._http, url, archive, self._sink, desc, parts=self._parts)
        except HttpStatusError as exc:
            raise RuntimeError(_install_failure(exc, major=major, image=image, os_name=os_name, arch=arch)) from exc

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
