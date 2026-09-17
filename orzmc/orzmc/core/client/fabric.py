"""Fabric client: resolve the fabric-loader profile and download its libraries."""

from __future__ import annotations

from orzmc.core.client.base import ClientPrepare, ClientProvider
from orzmc.core.fabric import Fabric
from orzmc.core.profiles import ProfileAddon
from orzmc.domain.types import GameType


class FabricProvider(ClientProvider):
    game_type = GameType.FABRIC

    def addon(self, prepare: ClientPrepare) -> ProfileAddon | None:
        addon = Fabric(prepare.cache, prepare.version).profile()
        for lib in addon.libraries:
            if lib.url:
                prepare.download(
                    lib.url,
                    prepare.paths.client_library_path(lib.path),
                    f"下载 {lib.name}",
                    lib.sha1,
                    force=prepare.force_download,
                )
        return addon


ClientProvider.register(FabricProvider())
