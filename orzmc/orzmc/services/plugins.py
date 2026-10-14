"""Hangar plugin service: search, list and download server plugins.

Backs ``orzmc plugin`` (spec ``docs/spec/cli-surface.md`` §4.3): ``--search``
lists matching projects, ``--list`` resolves the default server plugin set, and
``--output`` downloads each project's latest PAPER release into a directory.
"""

from __future__ import annotations

import os
from typing import Any

from orzmc.core.hangar import Hangar
from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.progress import NullProgress, ProgressSink
from orzmc.infra.transfer import download_with_progress

#: 默认服务器插件(与 Swift `PaperPlugin.all` 对齐)。
DEFAULT_PLUGINS: tuple[str, ...] = (
    "LuckPerms",
    "OrzMC",
    "LoginSecurity",
    "DeathChest",
    "GetMeHome",
    "SkinsRestorer",
    "GriefPrevention",
    "EzShops",
    "Essentials",
    "WorldEdit",
    "WorldGuard",
    "Vault",
    "ViaVersion",
    "ViaBackwards",
    "ViaRewind",
    "Geyser",
)


class PluginService:
    """Search / download server plugins from Hangar."""

    def __init__(
        self,
        http: HttpClient | None = None,
        fs: FileStore | None = None,
        reporter: Reporter | None = None,
        sink: ProgressSink | None = None,
    ) -> None:
        self._http = http or HttpClient()
        self._fs = fs or FileStore()
        self._reporter = reporter or NullReporter()
        self._sink = sink or NullProgress()
        self._hangar = Hangar(self._http)

    def search(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        """Projects matching ``text`` (raw Hangar project objects)."""
        return self._hangar.search(text, limit)

    def list_all(self) -> tuple[list[dict[str, Any]], list[str]]:
        """``(found_projects, not_found_names)`` for the default plugin set."""
        found: list[dict[str, Any]] = []
        not_found: list[str] = []
        for name in DEFAULT_PLUGINS:
            project = _match(self._hangar.search(name, 10), name)
            if project is None:
                not_found.append(name)
            else:
                found.append(project)
        return found, not_found

    def download(self, output_dir: str, version: str | None = None) -> list[str]:
        """Download every default plugin's latest PAPER jar into ``output_dir``.

        Returns the written file names. ``version`` pins a specific plugin
        version (Hangar version name), else the latest release is used.
        """
        self._fs.ensure_dir(output_dir)
        projects, _ = self.list_all()
        files: list[str] = []
        for project in projects:
            slug = _slug(project) or str(project.get("name") or "")
            resolved = self._hangar.download(slug, version)
            if resolved is None:
                continue
            name, url = resolved
            dest = os.path.join(output_dir, name)
            self._reporter.info(f"下载 {name}")
            download_with_progress(self._http, url, dest, self._sink, f"下载 {name}")
            files.append(name)
        return files


def _slug(project: dict[str, Any]) -> str | None:
    namespace = project.get("namespace") or {}
    slug = namespace.get("slug") if isinstance(namespace, dict) else None
    return str(slug) if slug else None


def _match(projects: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for project in projects:
        if project.get("name") == name:
            return project
    return None
