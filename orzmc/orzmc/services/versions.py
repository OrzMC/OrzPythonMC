"""Multi-version lifecycle: enumerate installed versions and remove them."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from orzmc.domain.paths import DEFAULT_ROOT
from orzmc.infra.fs import FileStore


class VersionNotFoundError(RuntimeError):
    """目标版本(或其客户端/某类型服务端)不存在。

    单独一个类型是为了让 CLI 报规范里的 ``E_NOT_FOUND``(§6 错误码表),而不是笼统的
    ``E_RUNTIME`` —— Swift 实现同样报 ``E_NOT_FOUND``。
    """


@dataclass(frozen=True)
class InstalledVersion:
    version: str
    has_client: bool = False
    server_types: tuple[str, ...] = ()

    def describe(self) -> str:
        parts: list[str] = []
        if self.has_client:
            parts.append("client")
        if self.server_types:
            parts.append("server(" + ",".join(self.server_types) + ")")
        return f"{self.version}: {', '.join(parts) if parts else '空'}"


class VersionManager:
    def __init__(self, fs: FileStore | None = None, root_dir: str | None = None) -> None:
        self._fs = fs or FileStore()
        self._root = root_dir or DEFAULT_ROOT

    def list_versions(self) -> list[InstalledVersion]:
        versions_dir = os.path.join(self._root, "versions")
        result: list[InstalledVersion] = []
        for version in self._fs.list_dir(versions_dir):
            vdir = os.path.join(versions_dir, version)
            if not self._fs.is_dir(vdir):
                continue
            has_client = self._fs.is_dir(os.path.join(vdir, "client"))
            server_root = os.path.join(vdir, "server")
            server_types = tuple(
                st for st in self._fs.list_dir(server_root) if self._fs.is_dir(os.path.join(server_root, st))
            )
            result.append(InstalledVersion(version=version, has_client=has_client, server_types=server_types))
        result.sort(key=lambda e: e.version, reverse=True)
        return result

    def remove(
        self,
        version: str,
        is_client: bool = True,
        game_type: str | None = None,
        yes: bool = False,
        confirm: Callable[[str], bool] | None = None,
    ) -> bool:
        """Remove a version's client or a specific server type.

        Returns True when something was removed. ``confirm`` (optional) is
        consulted unless ``yes`` is set.
        """
        if is_client:
            target = os.path.join(self._root, "versions", version, "client")
        else:
            if not game_type:
                raise ValueError("移除服务端必须指定类型 (-t)")
            target = os.path.join(self._root, "versions", version, "server", game_type)
        if not self._fs.exists(target):
            raise VersionNotFoundError(f"未找到要移除的目录: {target}")
        if not yes and confirm is not None and not confirm(f"移除 {target}?"):
            return False
        self._fs.remove(target)
        self._prune_empty(os.path.dirname(target))
        self._prune_empty(os.path.join(self._root, "versions", version))
        return True

    @staticmethod
    def _prune_empty(path: str) -> None:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
