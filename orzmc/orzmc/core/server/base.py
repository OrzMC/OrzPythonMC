"""CoreProvider strategy: obtain a server core jar for a game type.

``core/server`` sits below the services layer: providers receive every
dependency as an injected seam (``ServerPrepare``), so this package never
imports ``orzmc.services``. The two orchestration seams ``download`` and
``resolve_build_java`` are supplied by ``ServerService`` from the services
layer's ``Downloader`` / ``JavaEnv`` — a textbook dependency inversion.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

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
    """Resolve a sandboxed java binary for a build/install step (JavaEnv.resolve)."""

    def __call__(
        self, major: int, need_jdk: bool = False, confirm: Callable[[int, bool], bool] | None = None
    ) -> str: ...


@dataclass(frozen=True)
class ServerPrepare:
    """Injected seams + parsed Mojang data a CoreProvider needs to obtain a core."""

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


_REGISTRY: dict[GameType, CoreProvider] = {}


class CoreProvider(ABC):
    """Strategy that ensures the core jar exists at ``paths.server_jar_path()``.

    Concrete providers are stateless singletons that self-register on import.
    """

    game_type: GameType

    @abstractmethod
    def obtain(self, prepare: ServerPrepare) -> None:
        """Download or build the core jar; raise RuntimeError on failure."""

    @classmethod
    def register(cls, provider: CoreProvider) -> None:
        _REGISTRY[provider.game_type] = provider

    @classmethod
    def for_type(cls, game_type: GameType) -> CoreProvider | None:
        return _REGISTRY.get(game_type)
