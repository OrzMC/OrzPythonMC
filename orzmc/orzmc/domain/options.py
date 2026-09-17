"""Runtime options for a single client / server operation (pure data, no IO)."""

from __future__ import annotations

from dataclasses import dataclass

from orzmc.domain.types import GameType


@dataclass(frozen=True)
class RuntimeOptions:
    """Parsed CLI options that drive one launch or deployment.

    No IO, no side effects — a plain value object consumed by services.
    """

    is_client: bool = True
    version: str | None = None
    username: str = "guest"
    game_type: str = GameType.VANILLA.value
    min_mem: str = "512M"
    max_mem: str = "2G"
    extract_music: bool = False
    force_upgrade: bool = False
    force_download: bool = False
    # ``refresh`` bypasses the metadata cache (see infra.cache); CLI self-upgrade
    # is a separate command (``orzmc update``), never a launch/deploy option.
    refresh: bool = False
    symlink: bool = False
    jvm_opts: str | None = None
    server_args: str | None = None
    nogui: bool = False
    yes: bool = False
    root_dir: str | None = None

    @property
    def game_type_obj(self) -> GameType:
        return GameType.parse(self.game_type)
