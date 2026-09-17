"""Client launch use case: prepare files, assemble args, launch."""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from collections.abc import Callable

from orzmc.core.client import ClientPrepare, ClientProvider
from orzmc.core.profiles import ProfileAddon
from orzmc.domain.java import required_java_major
from orzmc.domain.launch import DEFAULT_MAIN_CLASS, build_launch_command
from orzmc.domain.libraries import resolve_libraries
from orzmc.services.context import Services


class ClientService:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._http = services.http
        self._fs = services.fs
        self._reporter = services.reporter
        self._paths = services.context.paths
        self._options = services.options

    def run(
        self,
        confirm_java: Callable[[int, bool], bool] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> int:
        version = self._options.version or ""
        self._reporter.info(f"客户端 {version} ({self._options.game_type})")
        downloader = self._services.downloader

        version_json = self._services.mojang.version_json(version)

        # Music extraction needs only the client jar — skip Java/assets/libraries.
        if self._options.extract_music:
            self._ensure_client_jar(version_json)
            return self._extract_music()

        major = required_java_major(version_json)
        java_bin = self._services.resolve_java(major, confirm=confirm_java)

        downloader.prepare_client(version_json)
        downloader.write_launcher_profiles(version, self._options.username)

        addon = self._resolve_addon(version_json, major, confirm_java)
        classpath = self._build_classpath(version_json, addon)
        main_class = (addon.main_class if addon else None) or version_json.get("mainClass") or DEFAULT_MAIN_CLASS
        cmd = build_launch_command(
            java_bin,
            version_json,
            self._paths,
            self._options,
            classpath,
            main_class,
            extra_jvm=addon.jvm_args if addon else None,
            extra_game=addon.game_args if addon else None,
        )
        self._reporter.success(f"开始启动客户端 {version}...")
        self._reporter.debug(" ".join(cmd))
        log_path = self._paths.client_launch_log_path()
        proc = self._services.process.run_detached(cmd, cwd=self._paths.client_dir(), log_path=log_path)
        pid = proc.pid
        # Give the JVM a short startup window. A healthy game takes far longer
        # than this to boot; if it exits within the window the launch config is
        # broken (bad natives, missing class, JVM crash), so surface the reason
        # instead of claiming "已启动".
        for _ in range(6):
            if proc.poll() is not None:
                raise RuntimeError(self._launch_failure_message(log_path))
            time.sleep(0.5)
        self._reporter.success(f"客户端已启动 (pid {pid}),日志: {log_path}")
        return 0

    # ── internals ───────────────────────────────────────────────────────────

    def _ensure_client_jar(self, version_json: dict) -> None:
        """Download only the client jar (music extraction needs nothing else)."""
        client = version_json.get("downloads", {}).get("client") or {}
        url = client.get("url")
        if not url:
            raise RuntimeError("该版本没有客户端 jar")
        self._services.downloader.download_file(
            url, self._paths.client_jar_path(), f"下载客户端 {self._options.version}", client.get("sha1")
        )

    def _launch_failure_message(self, log_path: str) -> str:
        """Human-readable reason for an immediately-exiting client launch."""
        tail = ""
        try:
            with open(log_path, "rb") as f:
                tail = f.read()[-4096:].decode("utf-8", errors="replace")
        except OSError:
            pass
        if tail.strip():
            return f"客户端启动后立即退出,请查看日志: {log_path}\n--- 日志尾部 ---\n{tail}"
        return f"客户端启动后立即退出(无日志输出),请查看: {log_path}"

    def _resolve_addon(
        self,
        version_json: dict,
        major: int,
        confirm_java: Callable[[int, bool], bool] | None,
    ) -> ProfileAddon | None:
        provider = ClientProvider.for_type(self._options.game_type_obj)
        if provider is None:
            raise ValueError(f"不支持的客户端类型: {self._options.game_type}")
        return provider.addon(self._client_prepare(version_json, major, confirm_java))

    def _client_prepare(
        self,
        version_json: dict,
        major: int,
        confirm_java: Callable[[int, bool], bool] | None,
    ) -> ClientPrepare:
        return ClientPrepare(
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

    def _build_classpath(self, version_json: dict, addon: ProfileAddon | None) -> list[str]:
        # Forge ships its own patched game jar; the vanilla jar must not shadow it.
        paths = [] if (addon and addon.uses_own_client_jar) else [self._paths.client_jar_path()]
        for lib in resolve_libraries(version_json):
            # Native jars stay on the classpath: since the modern Mojang format
            # (1.20.5+/26.x) the game self-extracts them from classpath jars
            # (LWJGL → SharedLibraryExtractPath, jtracy → tmpdir, netty →
            # native.workdir). Excluding them leaves liblwjgl.dylib etc. unfindable
            # and the client crashes with UnsatisfiedLinkError at startup.
            paths.append(self._paths.client_library_path(lib.path))
        if addon:
            for lib in addon.libraries:
                paths.append(self._paths.client_library_path(lib.path))
        return [p for p in paths if self._fs.is_file(p)]

    def _extract_music(self) -> int:
        jar = self._paths.client_jar_path()
        if not self._fs.is_file(jar):
            raise RuntimeError("客户端 jar 不存在,无法提取音乐")
        dest = self._paths.music_dir(self._options.version or "")
        self._fs.ensure_dir(dest)
        count = 0
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                if not name.endswith(".ogg"):
                    continue
                target = os.path.join(dest, name.replace("/", os.sep))
                self._fs.ensure_dir(os.path.dirname(target))
                with zf.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
        self._reporter.success(f"已提取 {count} 个音乐文件到 {dest}")
        return 0
