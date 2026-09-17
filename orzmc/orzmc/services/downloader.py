"""File preparation: client/server assets, libraries, natives."""

from __future__ import annotations

import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from orzmc.core.mojang import Mojang
from orzmc.domain.libraries import Library, resolve_libraries
from orzmc.domain.options import DEFAULT_DOWNLOAD_THREADS
from orzmc.domain.paths import PathLayout
from orzmc.infra.fs import FileStore
from orzmc.infra.hashing import sha1_file
from orzmc.infra.http import SMALL_FILE_READ_TIMEOUT, HttpClient
from orzmc.infra.log import Reporter
from orzmc.infra.progress import ProgressSink
from orzmc.infra.transfer import download_with_progress

_NATIVE_EXTS = (".dylib", ".dll", ".so", ".jnilib")


class Downloader:
    """Downloads and prepares all client/server files for a concrete version."""

    def __init__(
        self,
        http: HttpClient,
        fs: FileStore,
        reporter: Reporter,
        sink: ProgressSink,
        paths: PathLayout,
        workers: int = DEFAULT_DOWNLOAD_THREADS,
    ) -> None:
        self._http = http
        self._fs = fs
        self._reporter = reporter
        self._sink = sink
        self._paths = paths
        self._workers = workers

    # ── single file ─────────────────────────────────────────────────────────

    def download_file(self, url: str, dest: str, desc: str, sha1: str | None = None, force: bool = False) -> bool:
        """Download a single file (byte progress); skip when already valid.

        Pass ``force=True`` to re-download even when the cached file looks valid.
        """
        if not force and not self._needs_download(dest, sha1):
            self._reporter.debug(f"已存在,跳过: {desc}")
            return False
        download_with_progress(self._http, url, dest, self._sink, desc)
        if sha1 and sha1_file(dest) != sha1:
            self._fs.remove(dest)
            raise RuntimeError(f"文件校验失败: {desc}")
        self._reporter.debug(f"下载完成: {desc}")
        return True

    # ── client preparation ──────────────────────────────────────────────────

    def prepare_client(self, version_json: dict) -> None:
        """Download client jar, asset index + objects, libraries and natives."""
        paths = self._paths

        client = version_json.get("downloads", {}).get("client") or {}
        if client.get("url"):
            self.download_file(
                client["url"], paths.client_jar_path(), f"下载客户端 {paths.version}", client.get("sha1")
            )

        asset_index = version_json.get("assetIndex") or {}
        index_id = asset_index.get("id", "")
        if asset_index.get("url"):
            index_path = os.path.join(paths.client_indexes_dir(), f"{index_id}.json")
            self.download_file(asset_index["url"], index_path, "下载资源索引", asset_index.get("sha1"))
            if self._fs.is_file(index_path):
                self._download_asset_objects(self._fs.read_json(index_path))

        libraries = resolve_libraries(version_json)
        self._download_libraries(libraries)
        self._extract_natives(libraries)

    def _download_asset_objects(self, index: dict) -> None:
        paths = self._paths
        objects = index.get("objects", {})
        items = [
            (Mojang.assets_object_url(meta["hash"]), paths.client_object_path(meta["hash"]), meta.get("hash"))
            for meta in objects.values()
            if isinstance(meta, dict) and meta.get("hash")
        ]
        self._download_missing(items, f"下载资源文件({len(objects)})")

    def _download_libraries(self, libraries: list[Library]) -> None:
        paths = self._paths
        # Native jars are downloaded into libraries/ too — _extract_natives
        # unpacks them into natives/ afterwards.
        items = [(lib.url, paths.client_library_path(lib.path), lib.sha1) for lib in libraries if lib.url]
        self._download_missing(items, "下载依赖库")

    def _extract_natives(self, libraries: list[Library]) -> None:
        paths = self._paths
        natives_dir = paths.client_natives_dir()
        self._fs.ensure_dir(natives_dir)
        extracted = 0
        for lib in libraries:
            if not lib.is_native:
                continue
            jar = paths.client_library_path(lib.path)
            if not self._fs.is_file(jar):
                continue
            with zipfile.ZipFile(jar) as zf:
                for name in zf.namelist():
                    base = os.path.basename(name)
                    if not name.endswith("/") and (
                        name.endswith(_NATIVE_EXTS) or base.startswith("lib") or base in ("OpenAL32", "OpenAL64")
                    ):
                        target = os.path.join(natives_dir, name.replace("/", os.sep))
                        self._fs.ensure_dir(os.path.dirname(target))
                        with zf.open(name) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        extracted += 1
        if extracted:
            self._reporter.debug(f"已解压 {extracted} 个原生库到 {natives_dir}")

    def write_launcher_profiles(self, version: str, username: str) -> None:
        """Write a minimal official-launcher-style profiles file (the Forge installer reads it)."""
        profile_id = version if username == "guest" else f"{version}_{username}"
        launcher = {
            "authenticationDatabase": {},
            "selectedUser": {"account": "", "profile": ""},
            "profiles": {profile_id: {"name": f"{username} - {version}", "lastVersionId": version, "javaArgs": ""}},
            "settings": {"enableSnapshots": False, "keepLauncherOpen": False},
            "version": 1,
        }
        self._fs.write_json(self._paths.client_launcher_profiles_path(), launcher)

    # ── bulk download ───────────────────────────────────────────────────────

    def _download_missing(self, items: list[tuple[str, str, str | None]], desc: str) -> int:
        """Download url→dest for every missing/invalid file, concurrently.

        Returns the number of files actually downloaded.
        """
        missing = [(u, d, s) for u, d, s in items if self._needs_download(d, s)]
        if not missing:
            return 0
        self._sink.start(desc, total=len(missing))
        done = 0
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._download_one, u, d, s): d for u, d, s in missing}
            for future in as_completed(futures):
                future.result()  # re-raise download failures
                done += 1
                self._sink.advance(1)
        self._sink.finish()
        self._reporter.debug(f"{desc}: 完成 {done} 个")
        return done

    def _download_one(self, url: str, dest: str, sha1: str | None) -> None:
        self._fs.ensure_dir(os.path.dirname(dest))
        # 小文件专用短读超时:卡住的连接尽快失败重试,而不是让整批并发空等 30s。
        self._http.download(url, dest, timeout=SMALL_FILE_READ_TIMEOUT)
        if sha1 and sha1_file(dest) != sha1:
            self._fs.remove(dest)
            raise RuntimeError(f"文件校验失败: {url}")

    def _needs_download(self, dest: str, sha1: str | None) -> bool:
        if not self._fs.is_file(dest):
            return True
        if self._fs.file_size(dest) <= 0:
            return True
        return bool(sha1 and sha1_file(dest) != sha1)
