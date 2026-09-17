"""Forge server core: resolve via Maven API and run the official installer.

Two eras produce different launch layouts:

- **Modern (ForgeBootstrap, 1.20.4+ / 49.x)**: ``--installServer`` emits a
  directly-runnable ``forge-<full>-shim.jar`` whose manifest ``Class-Path``
  points at ``libraries/`` relative to itself, plus the full ``libraries/``
  tree. Both must move together into the server directory.
- **Legacy (pre-1.17)**: a single runnable universal jar
  ``forge-<full>.jar`` at the top level, as in the pre-shim era.
"""

from __future__ import annotations

import os

from orzmc.core.forge import Forge
from orzmc.core.server.base import CoreProvider, ServerPrepare
from orzmc.domain.types import GameType

_FORGE_MIN_JAVA = 17  # ForgeBootstrap-era installers require Java 17+


class ForgeProvider(CoreProvider):
    game_type = GameType.FORGE

    def obtain(self, prepare: ServerPrepare) -> None:
        # A complete install (jar + libraries/ tree) must not re-run the heavy
        # --installServer step on every invocation; --force opts back in.
        if not prepare.force_download and self._is_installed(prepare):
            prepare.reporter.info("Forge 服务端已安装,跳过安装器(用 --force 重装)")
            return
        forge = Forge(prepare.cache)
        full = forge.latest_full_version(prepare.version)
        build_dir = prepare.paths.server_build_dir()
        prepare.fs.ensure_dir(build_dir)
        installer = os.path.join(build_dir, f"forge-{full}-installer.jar")
        prepare.download(forge.installer_url(full), installer, f"下载 Forge 安装器 {full}")
        java_bin = prepare.resolve_build_java(
            max(prepare.major, _FORGE_MIN_JAVA), need_jdk=False, confirm=prepare.confirm_java
        )
        prepare.reporter.info(f"正在安装 Forge {full}...")
        code = prepare.process.run_stream(
            [java_bin, "-jar", installer, "--installServer"],
            on_line=lambda line: prepare.reporter.plain(line),
            cwd=build_dir,
        )
        if code != 0:
            raise RuntimeError(f"Forge 安装失败 ({full})")
        self._install_product(prepare, full, build_dir)

    def _is_installed(self, prepare: ServerPrepare) -> bool:
        """True when the server jar is present and, for the shim era, its
        libraries/ tree sits beside it (a partial move is not "installed")."""
        if not prepare.fs.is_file(prepare.paths.server_jar_path()):
            return False
        return prepare.fs.is_dir(os.path.join(prepare.paths.server_dir(), "libraries"))

    def _install_product(self, prepare: ServerPrepare, full: str, build_dir: str) -> None:
        """Move the generated server core(s) into the server directory."""
        server_dir = prepare.paths.server_dir()
        # Modern era: the shim launcher plus its libraries/ tree move together —
        # the manifest Class-Path is relative to the shim jar, so both must land
        # in the same directory and the shim keeps working under its new name.
        shim = os.path.join(build_dir, f"forge-{full}-shim.jar")
        if prepare.fs.is_file(shim):
            prepare.fs.move(shim, prepare.paths.server_jar_path())
            lib_src = os.path.join(build_dir, "libraries")
            lib_dst = os.path.join(server_dir, "libraries")
            if prepare.fs.is_dir(lib_src) and not prepare.fs.exists(lib_dst):
                prepare.fs.move(lib_src, lib_dst)
            return
        # Legacy era: one runnable universal jar at the top level.
        candidates = [
            os.path.join(build_dir, f"forge-{full}.jar"),
            os.path.join(build_dir, f"{prepare.version}-forge.jar"),
            os.path.join(build_dir, f"{prepare.version}-forge-universal.jar"),
        ]
        found = next((c for c in candidates if prepare.fs.is_file(c)), None)
        if not found:
            raise RuntimeError("未找到 Forge 服务端产物")
        prepare.fs.move(found, prepare.paths.server_jar_path())


CoreProvider.register(ForgeProvider())
