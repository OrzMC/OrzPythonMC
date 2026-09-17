"""Forge version resolution & installer helpers (Maven API — no HTML scraping).

The download page's own data source, ``promotions_slim.json``, maps a Minecraft
version to its recommended/latest Forge build. Everything else comes from the
Forge Maven repository, and the client launch definition (``version.json``) is
extracted from the installer jar it is shipped inside. Shared by the client and
server Forge providers.
"""

from __future__ import annotations

import json
import zipfile
from typing import Any

from orzmc.infra.cache import MetadataCache

PROMOTIONS_URL = "https://files.minecraftforge.net/maven/net/minecraftforge/forge/promotions_slim.json"
MAVEN_BASE = "https://maven.minecraftforge.net/net/minecraftforge/forge"


class Forge:
    """Resolve the Forge version for a MC version and locate installer artifacts."""

    def __init__(self, cache: MetadataCache) -> None:
        self._cache = cache

    def latest_full_version(self, mc_version: str) -> str:
        """``<mc>-<build>`` (e.g. ``1.20.4-49.2.8``) for ``mc_version``."""
        promos = (
            self._cache.get_json(
                self._cache.meta_path("forge-promotions"),
                PROMOTIONS_URL,
                desc="获取 Forge 版本列表",
            ).get("promos")
            or {}
        )
        for key in (f"{mc_version}-latest", f"{mc_version}-recommended"):
            build = promos.get(key)
            if build:
                return f"{mc_version}-{build}"
        raise RuntimeError(f"未找到 Minecraft {mc_version} 的 Forge 版本")

    def installer_url(self, full_version: str) -> str:
        return f"{MAVEN_BASE}/{full_version}/forge-{full_version}-installer.jar"


def extract_version_json(installer_path: str) -> dict[str, Any]:
    """Read the ``version.json`` embedded in a Forge installer jar."""
    with zipfile.ZipFile(installer_path) as zf, zf.open("version.json") as f:
        return json.load(f)
