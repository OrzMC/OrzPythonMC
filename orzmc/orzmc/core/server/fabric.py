"""Fabric server core: download fabric-installer and generate the launch jar.

The installer resolves the fabric-loader libraries itself, then emits
``fabric-server-launch.jar`` — a small wrapper that loads the vanilla server
jar from a sibling ``server.jar`` at launch. The installer no longer downloads
that vanilla jar, so this provider fetches it from the Mojang manifest (the
same source the vanilla provider uses); both jars must stay side by side in
the server directory.
"""

from __future__ import annotations

import os

from orzmc.core.fabric import Fabric
from orzmc.core.server.base import CoreProvider, ServerPrepare
from orzmc.domain.types import GameType

INSTALLER_BASE = "https://maven.fabricmc.net/net/fabricmc/fabric-installer"
_FABRIC_MIN_JAVA = 17  # modern fabric-loader versions require Java 17+


class FabricProvider(CoreProvider):
    game_type = GameType.FABRIC

    def obtain(self, prepare: ServerPrepare) -> None:
        server_dir = prepare.paths.server_dir()
        prepare.fs.ensure_dir(server_dir)

        server = prepare.version_json.get("downloads", {}).get("server") or {}
        server_url = server.get("url")
        if not server_url:
            raise RuntimeError("该版本没有官方服务端下载")
        prepare.download(
            server_url,
            os.path.join(server_dir, "server.jar"),
            f"下载服务端 {prepare.version}",
            sha1=server.get("sha1"),
            force=prepare.force_download,
        )

        fabric = Fabric(prepare.cache, prepare.version)
        loader = fabric.latest_loader_version()
        installer_version = fabric.latest_installer_version()
        build_dir = prepare.paths.server_build_dir()
        prepare.fs.ensure_dir(build_dir)
        installer = os.path.join(build_dir, f"fabric-installer-{installer_version}.jar")
        prepare.download(
            f"{INSTALLER_BASE}/{installer_version}/fabric-installer-{installer_version}.jar",
            installer,
            "下载 fabric-installer",
        )
        java_bin = prepare.resolve_build_java(
            max(prepare.major, _FABRIC_MIN_JAVA), need_jdk=False, confirm=prepare.confirm_java
        )
        prepare.reporter.info(f"正在生成 Fabric 服务端 (loader {loader})...")
        code = prepare.process.run_stream(
            [
                java_bin,
                "-jar",
                installer,
                "server",
                "-dir",
                server_dir,
                "-mcversion",
                prepare.version,
                "-loader",
                loader,
            ],
            on_line=lambda line: prepare.reporter.plain(line),
            cwd=server_dir,
        )
        if code != 0:
            raise RuntimeError(f"Fabric 服务端生成失败 ({prepare.version})")
        if not prepare.fs.is_file(prepare.paths.server_jar_path()):
            raise RuntimeError(f"未找到 Fabric 服务端产物: {prepare.paths.server_jar_path()}")


CoreProvider.register(FabricProvider())
