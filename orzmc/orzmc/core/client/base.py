"""ClientProvider strategy: resolve an optional launch addon for a game type.

Mirror of ``core/server``: providers receive every dependency as an injected
seam (``ClientPrepare``), so this package never imports ``orzmc.services``.
The orchestration seams ``download`` / ``resolve_build_java`` are supplied by
``ClientService`` from the services layer — a textbook dependency inversion.

Each concrete provider holds all knowledge of one client type in a single
file, so editing one type never affects another: vanilla is a no-op, fabric
wraps the fabric-meta profile json, forge runs the official installer and
harvests its generated client jar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from orzmc.core.profiles import ProfileAddon
from orzmc.domain.paths import PathLayout
from orzmc.domain.types import GameType
from orzmc.infra.cache import MetadataCache
from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import Reporter
from orzmc.infra.runner import ProcessRunner


class DownloadSeam(Protocol):
    """Single-file download that skips an already-valid cache (download_file)."""

    def __call__(self, url: str, dest: str, desc: str, sha1: str | None = None, force: bool = False) -> bool: ...


class BuildJavaSeam(Protocol):
    """Resolve a sandboxed java binary for an install step (JavaEnv.resolve)."""

    def __call__(
        self, major: int, need_jdk: bool = False, confirm: Callable[[int, bool], bool] | None = None
    ) -> str: ...


@dataclass(frozen=True)
class ClientPrepare:
    """Injected seams + parsed Mojang data a ClientProvider needs for an addon."""

    version: str
    version_json: dict[str, Any]
    major: int
    force_download: bool
    confirm_java: Callable[[int, bool], bool] | None
    # ── infra / domain seams ────────────────────────────────────────────────
    paths: PathLayout
    fs: FileStore
    reporter: Reporter
    http: HttpClient
    cache: MetadataCache
    process: ProcessRunner
    # ── orchestration injections (services layer) ───────────────────────────
    download: DownloadSeam
    resolve_build_java: BuildJavaSeam


_REGISTRY: dict[GameType, ClientProvider] = {}


class ClientProvider(ABC):
    """Strategy that produces a launch addon for ``game_type`` (or None).

    Concrete providers are stateless singletons that self-register on import.
    """

    game_type: GameType

    @abstractmethod
    def addon(self, prepare: ClientPrepare) -> ProfileAddon | None:
        """Resolve extra libraries/args/main-class; return None to launch vanilla."""

    @classmethod
    def register(cls, provider: ClientProvider) -> None:
        _REGISTRY[provider.game_type] = provider

    @classmethod
    def for_type(cls, game_type: GameType) -> ClientProvider | None:
        return _REGISTRY.get(game_type)
