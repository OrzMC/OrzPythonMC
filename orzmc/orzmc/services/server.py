"""Server deploy/run use case: obtain a core, accept EULA, launch."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable

from orzmc.core.server import CoreProvider, ServerPrepare
from orzmc.domain.java import required_java_major
from orzmc.domain.launch import memory_args, user_jvm_opts
from orzmc.domain.types import GameType
from orzmc.services.context import Services


class ServerService:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._http = services.http
        self._fs = services.fs
        self._reporter = services.reporter
        self._paths = services.context.paths
        self._options = services.options

    @property
    def game_type(self) -> GameType:
        return self._options.game_type_obj

    def run(
        self,
        confirm_java: Callable[[int, bool], bool] | None = None,
        confirm_eula: Callable[[], bool] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> int:
        version = self._options.version or ""
        gt = self.game_type
        self._reporter.info(f"服务端 {version} ({gt.value})")

        version_json = self._services.mojang.version_json(version)
        major = required_java_major(version_json)

        self._prepare(version_json, major, confirm_java, confirm_eula)

        java_bin = self._services.resolve_java(major, confirm=confirm_java)
        cmd = self._build_server_command(java_bin)

        self._reporter.success(f"启动服务端 {version} ({gt.value})...")
        if on_line is not None:
            return self._services.process.run_stream(cmd, on_line=on_line, cwd=self._paths.server_dir())
        return self._services.process.run_stream(
            cmd, on_line=lambda line: self._reporter.plain(line), cwd=self._paths.server_dir()
        )

    # ── preparation ─────────────────────────────────────────────────────────

    def _prepare(
        self,
        version_json: dict,
        major: int,
        confirm_java: Callable[[int, bool], bool] | None,
        confirm_eula: Callable[[], bool] | None,
    ) -> None:
        provider = CoreProvider.for_type(self.game_type)
        if provider is None:
            raise ValueError(f"不支持的服务端类型: {self.game_type.value}")
        provider.obtain(self._server_prepare(version_json, major, confirm_java))

        self._accept_eula(confirm_eula)
        self._write_server_properties()
        if self._options.symlink:
            self._maybe_symlink_world()
        if self._options.force_upgrade:
            self._reporter.info("已启用 --forceUpgrade(世界格式升级)")

    def _server_prepare(
        self,
        version_json: dict,
        major: int,
        confirm_java: Callable[[int, bool], bool] | None,
    ) -> ServerPrepare:
        """Wire the CoreProvider seams: services layer provides the orchestration."""
        return ServerPrepare(
            version=self._options.version or "",
            version_json=version_json,
            major=major,
            force_download=self._options.force_download,
            confirm_java=confirm_java,
            paths=self._paths,
            fs=self._fs,
            reporter=self._reporter,
            http=self._http,
            cache=self._services.cache,
            process=self._services.process,
            download=self._services.downloader.download_file,
            resolve_build_java=self._services.java_env.resolve,
        )

    def _accept_eula(self, confirm_eula: Callable[[], bool] | None) -> None:
        if self._options.yes:
            accepted = True
        elif confirm_eula is not None:
            accepted = bool(confirm_eula())
        else:
            accepted = False
        if not accepted:
            raise RuntimeError("需要接受 Minecraft EULA 才能启动服务端,请使用 --yes 或交互确认")
        self._fs.write_text(self._paths.server_eula_path(), "eula=true\n")

    def _write_server_properties(self) -> None:
        path = self._paths.server_properties_path()
        content = self._fs.read_text(path) if self._fs.is_file(path) else ""
        additions: list[str] = []
        if "online-mode" not in content:
            additions.append("online-mode=false")
        if "server-ip" not in content:
            additions.append("server-ip=")
        if additions:
            self._fs.write_text(path, content + ("\n" if content else "") + "\n".join(additions) + "\n")

    def _maybe_symlink_world(self) -> None:
        src = os.path.join(self._paths.worlds_backup_dir(), self._options.version or "")
        world = self._paths.server_world_dir()
        if not self._fs.is_dir(src):
            self._reporter.warn(f"未找到备份世界 {src},跳过软链接")
            return
        if self._fs.exists(world):
            self._reporter.warn("服务端世界目录已存在,跳过软链接")
            return
        self._fs.ensure_symlink(world, src)

    def _build_server_command(self, java_bin: str) -> list[str]:
        cmd = [java_bin, *user_jvm_opts(self._options), *memory_args(self._options)]
        cmd += ["-jar", self._paths.server_jar_path()]
        user_args = shlex.split(self._options.server_args) if self._options.server_args else []
        if self._options.nogui and "nogui" not in user_args:
            cmd.append("nogui")
        cmd += user_args
        if self._options.force_upgrade and "--forceUpgrade" not in cmd:
            cmd.append("--forceUpgrade")
        return cmd
