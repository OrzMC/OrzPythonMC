"""OrzMC core library.

This module is the **public API contract**. The application layer (orzmc-app)
and any external project should only import from here; internals live under
``domain`` / ``infra`` / ``core`` / ``services``.
"""

from __future__ import annotations

from collections.abc import Callable

from orzmc.core.mojang import VersionEntry
from orzmc.domain.java import DEFAULT_JAVA_MAJOR, required_java_major
from orzmc.domain.launch import DEFAULT_MAIN_CLASS, build_launch_command, game_args, jvm_args
from orzmc.domain.libraries import Library, resolve_libraries
from orzmc.domain.options import DEFAULT_DOWNLOAD_THREADS, MAX_DOWNLOAD_THREADS, RuntimeOptions
from orzmc.domain.paths import DEFAULT_ROOT, PathLayout
from orzmc.domain.types import GameType
from orzmc.infra.fs import FileStore
from orzmc.infra.log import NullReporter, Reporter, RichReporter
from orzmc.infra.progress import NullProgress, ProgressSink, RichProgress
from orzmc.infra.runner import ProcessRunner
from orzmc.services.backup import Backup
from orzmc.services.client import ClientService
from orzmc.services.context import AppContext, Services
from orzmc.services.selfinstall import InstallManifest, SelfUninstaller
from orzmc.services.selfupdate import SelfUpdater, UpdateCheck
from orzmc.services.server import ServerService
from orzmc.services.versions import InstalledVersion, VersionManager
from orzmc.version import __version__

__all__ = [
    "DEFAULT_DOWNLOAD_THREADS",
    "DEFAULT_JAVA_MAJOR",
    "DEFAULT_MAIN_CLASS",
    "DEFAULT_ROOT",
    "MAX_DOWNLOAD_THREADS",
    "AppContext",
    "Backup",
    "ClientService",
    "FileStore",
    "GameType",
    "InstallManifest",
    "InstalledVersion",
    "Library",
    "NullProgress",
    "NullReporter",
    "PathLayout",
    "ProgressSink",
    # protocols & impls
    "Reporter",
    "RichProgress",
    "RichReporter",
    # domain
    "RuntimeOptions",
    # services
    "SelfUpdater",
    "ServerService",
    "Services",
    "UpdateCheck",
    "VersionEntry",
    "VersionManager",
    # versions
    "__version__",
    "backup_world",
    "build_launch_command",
    "check_self_update",
    "deploy_server",
    "game_args",
    "install_java",
    "jvm_args",
    # entry points
    "launch_client",
    "list_versions",
    "remote_version_catalog",
    "remote_versions",
    "remove_version",
    "required_java_major",
    "resolve_libraries",
    "uninstall_self",
    "update_self",
]

# ── entry points ─────────────────────────────────────────────────────────────


def launch_client(
    options: RuntimeOptions,
    reporter: Reporter | None = None,
    sink: ProgressSink | None = None,
    confirm_java: Callable[[int, bool], bool] | None = None,
) -> int:
    """Launch the Minecraft client for ``options`` (auto-installs everything).

    ``confirm_java`` (optional) is consulted before installing a Java runtime.
    """
    base = Services(options, reporter=reporter, sink=sink)
    version = options.version or base.mojang.latest_release_id()
    if not version:
        raise RuntimeError("无法确定 Minecraft 版本")
    return ClientService(base.for_version(version)).run(confirm_java=confirm_java)


def deploy_server(
    options: RuntimeOptions,
    reporter: Reporter | None = None,
    sink: ProgressSink | None = None,
    confirm_java: Callable[[int, bool], bool] | None = None,
    confirm_eula: Callable[[], bool] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> int:
    """Deploy and run a server for ``options`` (auto-installs everything).

    ``confirm_eula`` (optional) accepts the Minecraft EULA interactively when
    ``options.yes`` is not set. ``on_line`` receives streamed server output.
    """
    base = Services(options, reporter=reporter, sink=sink)
    version = options.version or base.mojang.latest_release_id()
    if not version:
        raise RuntimeError("无法确定 Minecraft 版本")
    return ServerService(base.for_version(version)).run(
        confirm_java=confirm_java, confirm_eula=confirm_eula, on_line=on_line
    )


def list_versions(root_dir: str | None = None) -> list[InstalledVersion]:
    """Enumerate installed versions (client / server types) under ``root_dir``."""
    return VersionManager(root_dir=root_dir).list_versions()


def remote_version_catalog(root_dir: str | None = None, refresh: bool = False) -> list[VersionEntry]:
    """List all Mojang manifest versions (any type, newest first); installs nothing.

    The manifest is cached with a 24h TTL; ``refresh=True`` bypasses the cache
    and re-fetches from Mojang immediately.
    """
    options = RuntimeOptions(root_dir=root_dir or DEFAULT_ROOT, refresh=refresh)
    return Services(options).mojang.version_entries()


def remote_versions(root_dir: str | None = None, refresh: bool = False) -> list[str]:
    """List Mojang release version ids (cached manifest, installs nothing)."""
    return [e.id for e in remote_version_catalog(root_dir, refresh) if e.is_release]


def remove_version(
    version: str,
    *,
    is_client: bool = True,
    game_type: str | None = None,
    root_dir: str | None = None,
    yes: bool = False,
    reporter: Reporter | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> bool:
    """Remove a version's client or a specific server type. ``confirm`` is
    consulted unless ``yes`` is set."""
    manager = VersionManager(root_dir=root_dir)
    return manager.remove(version, is_client=is_client, game_type=game_type, yes=yes, confirm=confirm)


def uninstall_self(
    binary: str,
    *,
    root_dir: str | None = None,
    remove_root: bool = False,
    yes: bool = False,
    force: bool = False,
    reporter: Reporter | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> bool:
    """Uninstall the tool itself: remove ``binary``, restore PATH, optional data.

    Reads the install manifest written by the one-line installer. ``confirm``
    is consulted for game-data removal unless ``remove_root`` (delete without
    asking) or ``yes`` (default to keep) applies. Returns True when the binary
    was removed.
    """
    return SelfUninstaller(reporter=reporter, root_dir=root_dir).uninstall(
        binary, remove_root=remove_root, yes=yes, force=force, confirm=confirm
    )


def check_self_update(
    binary: str | None = None,
    *,
    version: str | None = None,
    reporter: Reporter | None = None,
) -> UpdateCheck:
    """Resolve the current upgrade target for the tool itself; installs nothing.

    Without ``version`` the latest GitHub Release is looked up (subject to the
    unauthenticated API rate limit); with it no API call happens at all.
    """
    return SelfUpdater(reporter=reporter, binary=binary).check(version)


def update_self(
    binary: str,
    *,
    version: str | None = None,
    file: str | None = None,
    yes: bool = False,
    force: bool = False,
    reporter: Reporter | None = None,
    sink: ProgressSink | None = None,
    process: ProcessRunner | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> UpdateCheck | None:
    """Upgrade the installed ``orzmc`` binary itself (not a Minecraft version).

    ``binary`` is the running executable (``sys.argv[0]``). ``file`` installs a
    local binary instead of downloading (offline / test seam) and requires
    ``version``. The swap itself is performed by a detached helper after this
    process exits (a PyInstaller onefile binary must not replace its own file),
    so a returned ``applied=True`` means "staged and handed off". Returns
    ``None`` when the user declined, or a check with ``applied=False`` when
    already up to date. ``confirm`` is consulted before the hand-off unless
    ``yes`` is set.
    """
    return SelfUpdater(reporter=reporter, sink=sink, process=process, binary=binary).update(
        version, file=file, yes=yes, force=force, confirm=confirm
    )


def backup_world(
    version: str,
    game_type: str = "vanilla",
    root_dir: str | None = None,
    reporter: Reporter | None = None,
) -> str:
    """Back up ``versions/<version>/server/<game_type>/world`` to ``backup/worlds/``."""
    return Backup(reporter=reporter, root_dir=root_dir).backup_world(version, game_type)


def install_java(
    major: int,
    *,
    need_jdk: bool = False,
    root_dir: str | None = None,
    reporter: Reporter | None = None,
    sink: ProgressSink | None = None,
    confirm: Callable[[int, bool], bool] | None = None,
) -> str:
    """Ensure a sandboxed Java ``major`` is installed under ``root_dir/java/``.

    Returns the managed ``bin/java`` path.
    """
    options = RuntimeOptions(root_dir=root_dir or DEFAULT_ROOT)
    return Services(options, reporter=reporter, sink=sink).java_env.resolve(major, need_jdk=need_jdk, confirm=confirm)
