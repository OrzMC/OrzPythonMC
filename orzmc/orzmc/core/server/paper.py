"""Paper server core: resolve the latest Paper build and download it.

Paper migrated to the Fill API (``fill.papermc.io/v3``); the legacy
``api.papermc.io/v2`` project/version/build endpoints are gone (410 Gone).
The Fill API groups versions by major key and exposes the concrete download
``url`` directly on the latest-build response (path-suffix construction no
longer works).
"""

from __future__ import annotations

from orzmc.core.server.base import CoreProvider, ServerPrepare
from orzmc.domain.types import GameType
from orzmc.infra.cache import MetadataCache

API_BASE = "https://fill.papermc.io/v3"


class PaperAPI:
    def __init__(self, cache: MetadataCache) -> None:
        self._cache = cache

    def download_url(self, mc_version: str) -> str:
        """Resolve the latest stable Paper build download URL for ``mc_version``."""
        project = self._cache.get_json(
            self._cache.meta_path("paper-project"), f"{API_BASE}/projects/paper", desc="获取 Paper 版本列表"
        )
        versions: dict[str, list[str]] = project.get("versions", {})
        matched = _match_version(versions, mc_version)
        if matched is None:
            raise RuntimeError(f"Paper 不支持 Minecraft {mc_version}")

        build = self._cache.get_json(
            self._cache.meta_path("paper-build", matched),
            f"{API_BASE}/projects/paper/versions/{matched}/builds/latest",
            desc=f"获取 Paper 构建 ({matched})",
        )
        downloads: dict[str, dict[str, str]] = build.get("downloads", {})
        server = downloads.get("server:default") or downloads.get("server:mojang") or {}
        url = server.get("url")
        if not url:
            raise RuntimeError(f"Paper {matched} 最新构建没有可下载的 jar")
        return url


def _match_version(available: dict[str, list[str]], mc_version: str) -> str | None:
    """Return a concrete Paper version matching ``mc_version``.

    The Fill API groups versions by major key (e.g. ``"1.20"`` maps to
    ``["1.20.6", "1.20.4", "1.20.2", ...]``, newest first).  ``mc_version`` may
    name a group itself (its newest concrete wins) or a concrete version
    within a group.
    """
    if mc_version in available:
        return _newest(available[mc_version])
    for key, concrete in available.items():
        if mc_version.startswith(f"{key}.") or mc_version.startswith(f"{key}-"):
            if mc_version in concrete:
                return mc_version
            return _newest(concrete)
    return None


def _newest(concrete: list[str]) -> str | None:
    return concrete[0] if concrete else None


class PaperProvider(CoreProvider):
    game_type = GameType.PAPER

    def obtain(self, prepare: ServerPrepare) -> None:
        url = PaperAPI(prepare.cache).download_url(prepare.version)
        prepare.download(
            url,
            prepare.paths.server_jar_path(),
            f"下载 Paper {prepare.version}",
            force=prepare.force_download,
        )


CoreProvider.register(PaperProvider())
