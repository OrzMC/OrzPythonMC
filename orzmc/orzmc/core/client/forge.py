"""Forge client: run the official installer and assemble the launch addon.

Forge ships no standalone client definition: the launch json is embedded in
the installer jar and the patched ``client`` classifier jar (the Minecraft
module the launcher needs) has **no downloadable source** — its ``url`` is
empty in ``version.json`` and Maven 404s it. It is produced by the installer's
binary-patching pipeline from the vanilla client jar.

So this provider mirrors the server side: download the installer, run it
headlessly (``--installClient``) into a temporary official-layout directory,
then harvest the generated jar plus the forge libraries into our own
``client/libraries/`` layout. The shared ``core.forge`` adapter resolves the
version from ``promotions_slim.json`` and reads ``version.json`` from the jar.

Only the modern ForgeBootstrap era (1.20.4+ / 49.x) is supported: those
versions ship a plain ``arguments`` block with no ``{{token}}`` templating and
exactly one generated jar. Older Forge (bootstraplauncher) versions raise a
clear error instead of producing a broken launch.
"""

from __future__ import annotations

import os
from typing import Any

from orzmc.core.client.base import ClientPrepare, ClientProvider
from orzmc.core.forge import Forge, extract_version_json
from orzmc.core.profiles import ProfileAddon
from orzmc.domain.libraries import Library
from orzmc.domain.types import GameType

_BOOTSTRAP_LAUNCHER = "cpw.mods.bootstraplauncher"
_FORGE_MIN_JAVA = 17  # ForgeBootstrap-era installers require Java 17+

_MINIMAL_LAUNCHER_PROFILE = {
    "authenticationDatabase": {},
    "selectedUser": {"account": "", "profile": ""},
    "profiles": {},
    "settings": {"enableSnapshots": False, "keepLauncherOpen": False},
    "version": 1,
}


class ForgeProvider(ClientProvider):
    game_type = GameType.FORGE

    def addon(self, prepare: ClientPrepare) -> ProfileAddon | None:
        forge = Forge(prepare.cache)
        full = forge.latest_full_version(prepare.version)
        installer = os.path.join(prepare.paths.client_dir(), "forge", f"forge-{full}-installer.jar")
        prepare.download(forge.installer_url(full), installer, f"下载 Forge 安装器 {full}")

        forge_json = extract_version_json(installer)
        if _BOOTSTRAP_LAUNCHER in str(forge_json.get("mainClass", "")):
            raise RuntimeError(
                f"Forge {full} 使用旧版启动器(BootstrapLauncher),暂不支持客户端启动"
                "(仅支持 ForgeBootstrap 系,1.20.4 及以上)"
            )

        install_dir = os.path.join(prepare.paths.client_dir(), "forge", "install")
        try:
            self._run_client_install(prepare, installer, full, install_dir)
            libraries = self._harvest_libraries(prepare, forge_json, install_dir)
        finally:
            prepare.fs.remove(install_dir)

        return ProfileAddon(
            libraries=libraries,
            jvm_args=_forge_string_args(forge_json, "jvm"),
            game_args=_forge_string_args(forge_json, "game"),
            main_class=forge_json.get("mainClass"),
            # the harvested client jar *is* the game jar; the vanilla jar must
            # not also sit on the classpath or its unpatched classes shadow it
            uses_own_client_jar=True,
        )

    # ── internals ───────────────────────────────────────────────────────────

    def _run_client_install(self, prepare: ClientPrepare, installer: str, full: str, install_dir: str) -> None:
        prepare.fs.ensure_dir(install_dir)
        profile = os.path.join(install_dir, "launcher_profiles.json")
        if not prepare.fs.is_file(profile):
            prepare.fs.write_json(profile, _MINIMAL_LAUNCHER_PROFILE)
        java_bin = prepare.resolve_build_java(
            max(prepare.major, _FORGE_MIN_JAVA), need_jdk=False, confirm=prepare.confirm_java
        )
        prepare.reporter.info(f"正在生成 Forge 客户端 {full}...")
        code = prepare.process.run_stream(
            [java_bin, "-jar", installer, "--installClient", install_dir],
            on_line=lambda line: prepare.reporter.plain(line),
            cwd=install_dir,
        )
        if code != 0:
            raise RuntimeError(f"Forge 客户端安装失败 ({full})")

    def _harvest_libraries(self, prepare: ClientPrepare, forge_json: dict[str, Any], install_dir: str) -> list[Library]:
        """Move the forge libraries (incl. the generated client jar) into our layout."""
        libraries: list[Library] = []
        for lib in forge_json.get("libraries", []):
            artifact = (lib.get("downloads") or {}).get("artifact") or {}
            path = artifact.get("path")
            name = lib.get("name")
            if not path or not name:
                continue
            src = os.path.join(install_dir, "libraries", path)
            if not prepare.fs.is_file(src):
                continue
            prepare.fs.move(src, prepare.paths.client_library_path(path))
            libraries.append(
                Library(
                    name=name,
                    path=path,
                    url=artifact.get("url") or "",
                    sha1=artifact.get("sha1"),
                    size=artifact.get("size"),
                )
            )
        if not any(lib.path.endswith("-client.jar") for lib in libraries):
            raise RuntimeError("未找到 Forge 客户端产物(client jar),安装可能不完整")
        return libraries


def _forge_string_args(forge_json: dict[str, Any], key: str) -> list[str]:
    """Plain-string entries of ``arguments.<key>`` (forge ships no rule dicts here)."""
    return [a for a in (forge_json.get("arguments") or {}).get(key, []) if isinstance(a, str)]


ClientProvider.register(ForgeProvider())
