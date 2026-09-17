"""Mojang version manifest & per-version JSON adapter.

Both metadata paths read through the shared :class:`MetadataCache`: the
manifest honours the library-wide TTL (24h, ``--refresh`` bypasses it), while
per-version JSONs stay content-addressed by sha1 and are re-downloaded only
when the hash no longer matches. ``version_json`` is the single Mojang metadata
entry point shared by the client and server launch flows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from orzmc.infra.cache import MetadataCache
from orzmc.infra.hashing import sha1_file
from orzmc.infra.transfer import download_with_progress

VERSION_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
ASSET_BASE_URL = "https://resources.download.minecraft.net/"


@dataclass(frozen=True)
class VersionEntry:
    """One entry in the Mojang version manifest (a version id + its raw type)."""

    id: str
    type: str

    @property
    def is_release(self) -> bool:
        return self.type == "release"

    @property
    def channel(self) -> str:
        """release vs snapshot — snapshots cover every non-release manifest type."""
        return "release" if self.is_release else "snapshot"


class Mojang:
    def __init__(self, cache: MetadataCache, manifest_path: str, versions_json_dir: str) -> None:
        self._cache = cache
        self._http = cache.http
        self._fs = cache.fs
        self._sink = cache.sink
        self._manifest_path = manifest_path
        self._versions_json_dir = versions_json_dir

    def load_manifest(self) -> dict[str, Any]:
        """Return the version manifest, reusing the on-disk copy while it is fresh.

        Freshness is the shared metadata TTL (24h) and ``--refresh`` forces a
        refetch; an unreachable network falls back to the cached copy.
        """
        try:
            data = self._cache.get_json(self._manifest_path, VERSION_MANIFEST_URL, desc="获取 Minecraft 版本清单")
        except Exception as exc:
            raise RuntimeError("无法获取 Minecraft 版本清单(请检查网络连接)") from exc
        return data

    def version_entries(self) -> list[VersionEntry]:
        """All manifest entries (any type), preserving Mojang's newest-first order."""
        manifest = self.load_manifest()
        return [VersionEntry(id=v["id"], type=v.get("type") or "unknown") for v in manifest.get("versions", [])]

    def release_version_ids(self) -> list[str]:
        return [e.id for e in self.version_entries() if e.is_release]

    def latest_release_id(self) -> str:
        ids = self.release_version_ids()
        return ids[0] if ids else ""

    def version_info(self, version_id: str) -> dict[str, Any] | None:
        manifest = self.load_manifest()
        for v in manifest.get("versions", []):
            if v.get("id") == version_id:
                return v
        return None

    def version_json_url_and_sha1(self, version_id: str) -> tuple[str, str] | None:
        """(url of <version>.json, its sha1).

        The sha1 is the manifest's ``sha1`` field when present; modern entries
        omit it, but then the URL is content-addressed (``.../packages/<sha1>/``),
        so the hash is recovered from the URL itself.
        """
        info = self.version_info(version_id)
        if not info or not info.get("url"):
            return None
        url = info["url"]
        return url, info.get("sha1") or _sha1_from_url(url)

    def version_json(self, version: str) -> dict[str, Any]:
        """Resolve, download (if missing/invalid), and parse a Mojang version JSON.

        This is the single Mojang metadata path shared by both the client and
        server launch flows: the JSON is fetched once and cached under
        ``cache/versions/<version>.json``, validated by sha1 on every reuse.
        """
        info = self.version_json_url_and_sha1(version)
        if not info:
            raise RuntimeError(f"未知的 Minecraft 版本: {version}")
        url, sha1 = info
        dest = os.path.join(self._versions_json_dir, f"{version}.json")
        if not self._cache.refresh and self._cache_valid(dest, sha1):
            return self._fs.read_json(dest)
        self._fs.ensure_dir(self._versions_json_dir)
        download_with_progress(self._http, url, dest, self._sink, f"下载版本元数据 {version}")
        if sha1 and sha1_file(dest) != sha1:
            self._fs.remove(dest)
            raise RuntimeError(f"版本元数据校验失败: {version}")
        return self._fs.read_json(dest)

    def _cache_valid(self, path: str, sha1: str) -> bool:
        if not self._fs.is_file(path) or self._fs.file_size(path) <= 0:
            return False
        return not sha1 or sha1_file(path) == sha1

    @staticmethod
    def assets_object_url(sha1: str) -> str:
        return ASSET_BASE_URL + sha1[:2] + "/" + sha1


def _sha1_from_url(url: str) -> str:
    """Recover the content hash from a Mojang ``.../packages/<sha1>/<name>`` URL."""
    marker = "/packages/"
    if marker not in url:
        return ""
    candidate = url.split(marker, 1)[1].split("/", 1)[0]
    if len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate):
        return candidate
    return ""
