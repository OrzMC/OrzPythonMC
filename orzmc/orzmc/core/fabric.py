"""Fabric loader resolution & library download (fabric-meta API).

Every fabric-meta response goes through the shared :class:`MetadataCache`, so
repeat launches reuse the loader list / profile while it is fresh (24h TTL,
``--refresh`` bypasses it).
"""

from __future__ import annotations

from typing import Any

from orzmc.core.profiles import ProfileAddon
from orzmc.domain.libraries import Library
from orzmc.infra.cache import MetadataCache

META_BASE = "https://meta.fabricmc.net/v2"


class Fabric:
    def __init__(self, cache: MetadataCache, version: str, loader: str | None = None) -> None:
        self._cache = cache
        self.version = version
        self.loader = loader

    def profile(self) -> ProfileAddon:
        """Resolve the fabric-loader profile json for this MC version."""
        loader_version = self.loader or self.latest_loader_version()
        # fabric-meta stopped accepting the installer version in this URL (404
        # for every combo); the loader version alone resolves the profile.
        url = f"{META_BASE}/versions/loader/{self.version}/{loader_version}/profile/json"
        config: dict[str, Any] = self._cache.get_json(
            self._cache.meta_path("fabric-profile", self.version, loader_version),
            url,
            desc=f"获取 Fabric 配置 ({self.version})",
        )

        libraries: list[Library] = []
        for lib in config.get("libraries", []):
            name = lib.get("name")
            if not name:
                continue
            # fabric-meta's `url` is now just the repository base (e.g.
            # ``https://maven.fabricmc.net/``), not a per-artifact url — the
            # local path must come from the maven coordinates themselves.
            path = _coordinate_path(name)
            base = lib.get("url") or "https://maven.fabricmc.net/"
            libraries.append(
                Library(
                    name=name,
                    path=path,
                    url=base.rstrip("/") + "/" + path,
                    sha1=lib.get("sha1"),
                    size=lib.get("size"),
                )
            )

        arguments = config.get("arguments", {})
        jvm_args = [a for a in arguments.get("jvm", []) if isinstance(a, str)]
        game_args = [a for a in arguments.get("game", []) if isinstance(a, str)]
        main_class = _main_class(config)

        return ProfileAddon(libraries=libraries, jvm_args=jvm_args, game_args=game_args, main_class=main_class)

    def latest_loader_version(self) -> str:
        """Latest stable fabric-loader version for this MC version."""
        entries = self._cache.get_json(
            self._cache.meta_path("fabric-loader", self.version),
            f"{META_BASE}/versions/loader/{self.version}",
            desc=f"获取 Fabric loader 列表 ({self.version})",
        )
        for entry in entries:
            loader = (entry or {}).get("loader", {})
            if loader.get("stable"):
                return loader["version"]
        if entries:
            return entries[0]["loader"]["version"]
        raise RuntimeError(f"Fabric 不支持 Minecraft {self.version}")

    def latest_installer_version(self) -> str:
        """Latest stable fabric-installer version."""
        entries = self._cache.get_json(
            self._cache.meta_path("fabric-installer"),
            f"{META_BASE}/versions/installer",
            desc="获取 Fabric 安装器版本",
        )
        for entry in entries:
            if entry.get("stable"):
                return entry["version"]
        return entries[0]["version"]


def _coordinate_path(name: str) -> str:
    """Map maven coordinates ``group:artifact:version[:classifier]`` to a jar path."""
    parts = name.split(":")
    if len(parts) == 3:
        group, artifact, version = parts
        classifier = ""
    elif len(parts) == 4:
        group, artifact, version, classifier = parts
    else:
        return name.replace(":", "/") + ".jar"
    base = f"{artifact}-{version}"
    if classifier:
        base += f"-{classifier}"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{base}.jar"


def _main_class(config: dict[str, Any]) -> str | None:
    main_class = config.get("mainClass")
    if isinstance(main_class, str):
        return main_class
    if isinstance(main_class, dict):
        return main_class.get("client") or main_class.get("server")
    return None
