"""Services: JavaEnv, Downloader, ServerService arg assembly, ClientService classpath.

All tests use a real tmp root + fakes for network; no system java is touched.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import threading
import time
import zipfile

from fakes import FakeHttp, FakeProcess, FakeReporter, FakeSink

from orzmc import (
    DEFAULT_DOWNLOAD_THREADS,
    MAX_DOWNLOAD_THREADS,
    ClientService,
    FileStore,
    GameType,
    PathLayout,
    RuntimeOptions,
    ServerService,
    Services,
    resolve_libraries,
)
from orzmc.core.forge import PROMOTIONS_URL
from orzmc.core.mojang import VERSION_MANIFEST_URL
from orzmc.core.server import CoreProvider
from orzmc.core.server.base import ServerPrepare
from orzmc.core.server.paper import PaperAPI
from orzmc.domain.libraries import os_arch, os_key
from orzmc.infra.cache import MetadataCache
from orzmc.infra.http import SMALL_FILE_READ_TIMEOUT
from orzmc.services.java import JavaEnv


def _services(tmp_path, reporter: FakeReporter, sink: FakeSink, http: FakeHttp, **options) -> Services:
    base = RuntimeOptions(root_dir=str(tmp_path), version="1.20.4", **options)
    return Services(base, reporter=reporter, sink=sink, http=http, fs=FileStore())


def _cache(tmp_path, http: FakeHttp, **kwargs) -> MetadataCache:
    """A MetadataCache for direct adapter tests (PaperAPI / Fabric / Forge)."""
    fs = FileStore()
    return MetadataCache(http, fs, PathLayout(root=str(tmp_path)).cache_dir(), **kwargs)


class TestJavaEnv:
    def test_resolve_existing_runtime(self, tmp_path, reporter, sink) -> None:
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path))
        fs.write_text(paths.java_bin(17), "#!/bin/sh\n")
        http = FakeHttp()
        env = JavaEnv(http, fs, reporter, sink, paths)
        assert env.resolve(17, need_jdk=True) == paths.java_bin(17)
        assert http.requests == []

    def test_resolve_refused_by_confirm(self, tmp_path, reporter, sink) -> None:
        env = JavaEnv(FakeHttp(), FileStore(), reporter, sink, PathLayout(root=str(tmp_path)))
        with pytest_raises(RuntimeError):
            env.resolve(17, confirm=lambda major, jdk: False)

    def test_resolve_installs_and_caches(self, tmp_path, reporter, sink, http) -> None:
        # seed a fake JDK archive (Adoptium layout: single top-level dir)
        archive = _make_archive(
            {
                f"jdk-17.0.1/bin/{_java_exe()}": "#!/bin/sh\necho 17\n",
                "jdk-17.0.1/release": "JAVA_VERSION=17\n",
            }
        )
        http.canned_archive = archive
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path))
        env = JavaEnv(http, fs, reporter, sink, paths)
        java = env.resolve(17, need_jdk=True)
        assert java == paths.java_bin(17)
        assert fs.is_file(java)
        # second resolve must not hit the network
        http.requests.clear()
        assert env.resolve(17) == java
        assert http.requests == []

    def test_install_uses_jre_by_default(self, tmp_path, reporter, sink, http) -> None:
        http.canned_archive = _make_archive({f"jre-8/bin/{_java_exe()}": "java\n"})
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path))
        env = JavaEnv(http, fs, reporter, sink, paths)
        env.resolve(8, need_jdk=False)
        assert fs.is_file(paths.java_bin(8))

    def test_resolve_java_uses_jre_for_every_type(self, tmp_path, reporter, sink, http) -> None:
        # Every supported type runs on a sandboxed JRE — no game type needs a
        # full JDK at run time (fabric/forge install steps use JREs too).
        http.canned_archive = _make_archive({f"jre-21/bin/{_java_exe()}": "java\n"})
        for game_type in ("vanilla", "paper", "fabric", "forge"):
            services = _services(tmp_path, reporter, sink, http, game_type=game_type)
            assert services.resolve_java(21) == services.context.paths.java_bin(21)
            assert any("JRE 21" in text for text in reporter.texts)


class TestMojangMeta:
    def test_version_json_shared_cache(self, tmp_path, reporter, sink, http) -> None:
        # Both client and server launch resolve the version JSON through this one
        # Mojang path, cached once under cache/versions/ — never a per-role copy.
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        services.fs.write_json(
            paths.version_manifest_path(),
            {"versions": [{"id": "1.20.4", "type": "release", "url": "https://meta/1.20.4.json", "sha1": ""}]},
        )
        http.canned_archive = b'{"javaVersion": {"majorVersion": 17}}'
        parsed = services.mojang.version_json("1.20.4")
        assert parsed["javaVersion"]["majorVersion"] == 17
        assert services.fs.is_file(os.path.join(paths.version_jsons_dir(), "1.20.4.json"))
        # second call is served from cache — no network traffic
        http.requests.clear()
        assert services.mojang.version_json("1.20.4") == parsed
        assert http.requests == []

    def test_version_json_url_sha1_recovered_from_content_address(self, tmp_path, reporter, sink, http) -> None:
        # modern manifest entries drop the `sha1` field; the hash lives in the
        # content-addressed URL and must still drive cache validation.
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        url_sha = "c75d82e7fa6eca5a043dab0c6cf77cb8317644f4"
        services.fs.write_json(
            paths.version_manifest_path(),
            {
                "versions": [
                    {
                        "id": "26.2",
                        "type": "release",
                        "url": f"https://piston-meta.mojang.com/v1/packages/{url_sha}/26.2.json",
                    }
                ]
            },
        )
        url_and_sha1 = services.mojang.version_json_url_and_sha1("26.2")
        assert url_and_sha1 is not None
        assert url_and_sha1[1] == url_sha

    def test_version_json_content_address_cache_hit(self, tmp_path, reporter, sink, http) -> None:
        # a cached JSON whose hash matches the content-addressed URL is reused
        body = b'{"javaVersion": {"majorVersion": 25}}'
        url_sha = hashlib.sha1(body).hexdigest()
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        services.fs.write_json(
            paths.version_manifest_path(),
            {"versions": [{"id": "26.2", "type": "release", "url": f"https://meta/packages/{url_sha}/26.2.json"}]},
        )
        services.fs.ensure_dir(paths.version_jsons_dir())
        services.fs.write_text(os.path.join(paths.version_jsons_dir(), "26.2.json"), body.decode())
        assert services.mojang.version_json("26.2")["javaVersion"]["majorVersion"] == 25
        assert http.requests == []

    def test_version_json_cache_invalidated_on_sha1_mismatch(self, tmp_path, reporter, sink, http) -> None:
        # a stale cached JSON must be re-downloaded and sha1-revalidated
        body = b'{"javaVersion": {"majorVersion": 25}}'
        url_sha = hashlib.sha1(body).hexdigest()
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        services.fs.write_json(
            paths.version_manifest_path(),
            {"versions": [{"id": "26.2", "type": "release", "url": f"https://meta/packages/{url_sha}/26.2.json"}]},
        )
        cache = os.path.join(paths.version_jsons_dir(), "26.2.json")
        services.fs.ensure_dir(paths.version_jsons_dir())
        services.fs.write_text(cache, "stale")
        http.canned_archive = body
        assert services.mojang.version_json("26.2")["javaVersion"]["majorVersion"] == 25
        assert services.fs.read_text(cache) == body.decode()

    def test_version_entries_include_all_types_in_manifest_order(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        services.fs.write_json(
            services.context.paths.version_manifest_path(),
            {
                "versions": [
                    {"id": "26.2", "type": "release"},
                    {"id": "25w14a", "type": "snapshot"},
                    {"id": "b1.7.3", "type": "old_beta"},
                ]
            },
        )
        entries = services.mojang.version_entries()
        assert [(e.id, e.type, e.channel) for e in entries] == [
            ("26.2", "release", "release"),
            ("25w14a", "snapshot", "snapshot"),
            ("b1.7.3", "old_beta", "snapshot"),
        ]

    def test_version_entries_missing_type_defaults_to_unknown(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        services.fs.write_json(
            services.context.paths.version_manifest_path(),
            {"versions": [{"id": "x", "url": "https://meta/x.json"}]},
        )
        entry = services.mojang.version_entries()[0]
        assert (entry.id, entry.type, entry.is_release) == ("x", "unknown", False)

    def test_release_version_ids_delegates_to_entries(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        services.fs.write_json(
            services.context.paths.version_manifest_path(),
            {
                "versions": [
                    {"id": "1.21.4", "type": "release"},
                    {"id": "1.21.3", "type": "release"},
                    {"id": "1.21.2", "type": "snapshot"},
                    {"id": "1.20.4", "type": "release"},
                ]
            },
        )
        assert services.mojang.release_version_ids() == ["1.21.4", "1.21.3", "1.20.4"]


class TestMetadataCacheWiring:
    def test_manifest_cache_is_reused_across_runs(self, tmp_path, reporter, sink, http) -> None:
        # a second invocation must read the cached manifest, not the network
        http.json_responses = {VERSION_MANIFEST_URL: {"versions": [{"id": "1.20.4", "type": "release"}]}}
        first = _services(tmp_path, reporter, sink, http)
        assert first.mojang.release_version_ids() == ["1.20.4"]
        calls = len(http.json_calls)
        second = _services(tmp_path, reporter, sink, http)
        assert second.mojang.release_version_ids() == ["1.20.4"]
        assert len(http.json_calls) == calls

    def test_options_refresh_reaches_the_shared_cache(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, refresh=True)
        assert services.cache.refresh is True
        # every provider reads that cache, and ``for_version`` keeps the flag
        assert services.for_version("1.20.4").cache.refresh is True

    def test_refresh_flag_bypasses_the_cached_manifest(self, tmp_path, reporter, sink, http) -> None:
        http.json_responses = {VERSION_MANIFEST_URL: {"versions": [{"id": "1.20.4", "type": "release"}]}}
        _services(tmp_path, reporter, sink, http).mojang.release_version_ids()
        calls = len(http.json_calls)
        refreshed = _services(tmp_path, reporter, sink, http, refresh=True)
        refreshed.mojang.release_version_ids()
        assert len(http.json_calls) == calls + 1


class TestDownloader:
    def test_prepare_client(self, tmp_path, reporter, sink, http) -> None:
        http.canned_archive = b"{}"  # used for both the client jar and the index json
        services = _services(tmp_path, reporter, sink, http)
        version_json = {
            "downloads": {"client": {"url": "https://client.jar", "sha1": None}},
            "assetIndex": {"id": "1.20", "url": "https://index.json", "sha1": None},
            "libraries": [],
            "arguments": {"game": [], "jvm": []},
        }
        services.downloader.prepare_client(version_json)
        paths = services.context.paths
        assert services.fs.is_file(paths.client_jar_path())
        assert services.fs.is_file(os.path.join(paths.client_indexes_dir(), "1.20.json"))
        # asset objects index was read (empty) → nothing else downloaded
        assert len(http.requests) == 2

    def test_download_file_skips_existing(self, tmp_path, reporter, sink, http) -> None:
        http.canned_archive = b"data"
        services = _services(tmp_path, reporter, sink, http)
        dest = services.context.paths.client_jar_path()
        assert services.downloader.download_file("https://x", dest, "x")
        assert not services.downloader.download_file("https://x", dest, "x")  # cached
        assert len(http.requests) == 1


class _ConcurrencyProbe(FakeHttp):
    """记录同时在跑的 download 数:证明并发数真的生效。"""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def download(self, url, dest_path, on_chunk=None, on_open=None, timeout=None) -> int:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.05)  # 模拟每个小文件的网络往返
            return super().download(url, dest_path, on_chunk, on_open, timeout)
        finally:
            with self._lock:
                self.active -= 1


class TestDownloadConcurrency:
    """并发数与 keep-alive 连接池必须匹配(池小于并发 → 每个请求重建 TLS)。"""

    def test_runtime_options_default_comes_from_the_shared_constant(self) -> None:
        assert RuntimeOptions().download_threads == DEFAULT_DOWNLOAD_THREADS

    def test_configured_threads_reach_the_downloader(self, tmp_path, reporter, sink) -> None:
        services = _services(tmp_path, reporter, sink, FakeHttp(), download_threads=40)
        assert services.downloader._workers == 40

    def test_default_http_pool_covers_the_configured_threads(self, tmp_path, reporter, sink) -> None:
        options = RuntimeOptions(root_dir=str(tmp_path), version="1.20.4", download_threads=MAX_DOWNLOAD_THREADS)
        services = Services(options, reporter=reporter, sink=sink)
        assert services.http.pool_size >= MAX_DOWNLOAD_THREADS

    def test_bulk_download_runs_concurrently_within_the_cap(self, tmp_path, reporter, sink) -> None:
        http = _ConcurrencyProbe()
        urls = [f"https://assets/{i}" for i in range(8)]
        http.canned = dict.fromkeys(urls, b"x")
        services = _services(tmp_path, reporter, sink, http, download_threads=4)
        items: list[tuple[str, str, str | None]] = [
            (url, str(tmp_path / f"obj{i}"), None) for i, url in enumerate(urls)
        ]
        assert services.downloader._download_missing(items, "批量") == 8
        assert 2 <= http.peak <= 4  # 真的并行,且不超过配置的并发数

    def test_bulk_download_applies_the_small_file_timeout(self, tmp_path, reporter, sink, http) -> None:
        http.canned = {"https://assets/a": b"a"}
        services = _services(tmp_path, reporter, sink, http)
        services.downloader._download_missing([("https://assets/a", str(tmp_path / "a"), None)], "批量")
        assert [kw["timeout"] for kw in http.download_kwargs] == [SMALL_FILE_READ_TIMEOUT]


class TestServerService:
    def test_build_server_command(self, tmp_path, reporter, sink, http) -> None:
        services = _services(
            tmp_path,
            reporter,
            sink,
            http,
            server_args="nogui --port 25565",
            jvm_opts="-XX:+UseZGC",
            force_upgrade=True,
        )
        server = ServerService(services)
        cmd = server._build_server_command("/managed/java")
        assert cmd[0] == "/managed/java"
        assert cmd.index("-XX:+UseZGC") < cmd.index("-jar")
        assert "-Xmx2G" in cmd
        assert "-jar" in cmd
        assert "nogui" in cmd and "--port" in cmd and "25565" in cmd
        assert cmd[-1] == "--forceUpgrade"

    def test_build_server_command_nogui_flag(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, nogui=True, server_args="--port 25565")
        cmd = ServerService(services)._build_server_command("/managed/java")
        assert cmd.index("nogui") > cmd.index("-jar")
        assert cmd.index("nogui") < cmd.index("--port")
        assert cmd.count("nogui") == 1

    def test_build_server_command_nogui_not_duplicated(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, nogui=True, server_args="nogui --port 25565")
        cmd = ServerService(services)._build_server_command("/managed/java")
        assert cmd.count("nogui") == 1

    def test_build_server_command_nogui_default_absent(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, server_args="--port 25565")
        cmd = ServerService(services)._build_server_command("/managed/java")
        assert "nogui" not in cmd

    def test_accept_eula_via_yes(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, yes=True)
        server = ServerService(services)
        server._accept_eula(confirm_eula=None)
        assert services.fs.read_text(services.context.paths.server_eula_path()) == "eula=true\n"

    def test_accept_eula_requires_yes_or_confirm(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, yes=False)
        server = ServerService(services)
        with pytest_raises(RuntimeError):
            server._accept_eula(confirm_eula=None)

    def test_accept_eula_uses_confirm(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http, yes=False)
        server = ServerService(services)
        server._accept_eula(confirm_eula=lambda: True)
        assert services.fs.read_text(services.context.paths.server_eula_path()) == "eula=true\n"

    def test_write_server_properties_idempotent(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        server = ServerService(services)
        server._write_server_properties()
        server._write_server_properties()
        content = services.fs.read_text(services.context.paths.server_properties_path())
        assert content.count("online-mode=false") == 1


class TestCoreProvider:
    def test_registry_covers_all_game_types(self) -> None:
        # every game type must map to a registered CoreProvider
        for gt in GameType:
            provider = CoreProvider.for_type(gt)
            assert provider is not None, f"缺少 provider: {gt}"
            assert provider.game_type == gt

    def test_server_prepare_wires_services_seams(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        server = ServerService(services)
        prepare = server._server_prepare({"javaVersion": {"majorVersion": 17}}, 17, confirm_java=None)
        assert prepare.version == "1.20.4"
        assert prepare.paths is services.context.paths
        assert prepare.process is services.process
        assert prepare.download == services.downloader.download_file
        assert prepare.resolve_build_java == services.java_env.resolve

    def test_vanilla_provider_rejects_missing_server_entry(self, tmp_path, reporter, sink, http) -> None:
        # a version JSON without downloads.server must raise a clear error
        services = _services(tmp_path, reporter, sink, http)
        prepare = ServerService(services)._server_prepare({"downloads": {}}, 8, confirm_java=None)
        provider = CoreProvider.for_type(GameType.VANILLA)
        assert provider is not None
        with pytest_raises(RuntimeError):
            provider.obtain(prepare)


def _server_prepare(
    tmp_path,
    *,
    http: FakeHttp,
    process: FakeProcess,
    version_json: dict | None = None,
    game_type: str = "vanilla",
    version: str = "1.20.4",
) -> ServerPrepare:
    """Wire a ServerPrepare with fake seams for provider-level tests."""
    paths = PathLayout(root=str(tmp_path), version=version, game_type=game_type)
    fs = FileStore()

    def fake_download(url: str, dest: str, desc: str, sha1: str | None = None, force: bool = False) -> bool:
        fs.ensure_dir(os.path.dirname(dest))
        with open(dest, "wb") as f:
            f.write(b"fake jar")
        return True

    return ServerPrepare(
        version=version,
        version_json=version_json or {"downloads": {"server": {"url": "https://mojang/server.jar"}}},
        major=17,
        force_download=False,
        confirm_java=None,
        paths=paths,
        fs=fs,
        reporter=FakeReporter(),
        http=http,
        cache=MetadataCache(http, fs, paths.cache_dir()),
        process=process,
        download=fake_download,
        resolve_build_java=lambda major, need_jdk=False, confirm=None: "/fake/java",
    )


class TestServerProviders:
    def test_fabric_server_downloads_vanilla_and_runs_installer(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            "/versions/loader/1.20.4": [{"loader": {"version": "0.19.3", "stable": True}}],
            "/versions/installer": [{"version": "1.1.2", "stable": True}],
        }
        paths = PathLayout(root=str(tmp_path), version="1.20.4", game_type="fabric")
        launch_jar = paths.server_jar_path()  # server/fabric/fabric-server-launch.jar
        process = FakeProcess(created=[launch_jar])
        prepare = _server_prepare(tmp_path, http=http, process=process, game_type="fabric")
        provider = CoreProvider.for_type(GameType.FABRIC)
        assert provider is not None
        provider.obtain(prepare)

        # the Mojang-manifest vanilla server sits next to the fabric launcher
        assert prepare.fs.is_file(os.path.join(paths.server_dir(), "server.jar"))
        assert prepare.fs.is_file(launch_jar)
        cmd = process.last_cmd
        assert cmd is not None and "server" in cmd
        i = cmd.index("server")
        assert cmd[i + 1 : i + 5] == ["-dir", paths.server_dir(), "-mcversion", "1.20.4"]

    def test_fabric_server_missing_launch_jar_raises(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            "/versions/loader/1.20.4": [{"loader": {"version": "0.19.3", "stable": True}}],
            "/versions/installer": [{"version": "1.1.2", "stable": True}],
        }
        process = FakeProcess()  # creates nothing
        prepare = _server_prepare(tmp_path, http=http, process=process, game_type="fabric")
        provider = CoreProvider.for_type(GameType.FABRIC)
        assert provider is not None
        with pytest_raises(RuntimeError, match="未找到 Fabric 服务端产物"):
            provider.obtain(prepare)

    def test_forge_server_modern_shim_layout(self, tmp_path) -> None:
        # ForgeBootstrap era: installer produces a shim jar + libraries/ tree;
        # both must move into the server directory so the shim's relative
        # Class-Path keeps resolving under its renamed server_jar_name.
        http = FakeHttp()
        http.json_responses[PROMOTIONS_URL] = {"promos": {"1.20.4-latest": "49.2.8"}}
        paths = PathLayout(root=str(tmp_path), version="1.20.4", game_type="forge")
        build_dir = paths.server_build_dir()
        shim = os.path.join(build_dir, "forge-1.20.4-49.2.8-shim.jar")
        lib_file = os.path.join(build_dir, "libraries", "net", "minecraftforge", "a.jar")
        process = FakeProcess(created=[shim, lib_file])
        prepare = _server_prepare(tmp_path, http=http, process=process, game_type="forge")
        provider = CoreProvider.for_type(GameType.FORGE)
        assert provider is not None
        provider.obtain(prepare)

        assert prepare.fs.is_file(paths.server_jar_path())  # shim moved + renamed
        assert prepare.fs.is_file(os.path.join(paths.server_dir(), "libraries", "net", "minecraftforge", "a.jar"))
        assert process.last_cmd is not None and "--installServer" in process.last_cmd

    def test_forge_server_legacy_universal_jar(self, tmp_path) -> None:
        # pre-1.17 era: a single runnable universal jar at the build dir top level
        http = FakeHttp()
        http.json_responses[PROMOTIONS_URL] = {"promos": {"1.16.5-latest": "36.2.39"}}
        paths = PathLayout(root=str(tmp_path), version="1.16.5", game_type="forge")
        universal = os.path.join(paths.server_build_dir(), "forge-1.16.5-36.2.39.jar")
        process = FakeProcess(created=[universal])
        prepare = _server_prepare(tmp_path, http=http, process=process, game_type="forge", version="1.16.5")
        provider = CoreProvider.for_type(GameType.FORGE)
        assert provider is not None
        provider.obtain(prepare)
        assert prepare.fs.is_file(paths.server_jar_path())

    def test_forge_server_skips_installer_when_installed(self, tmp_path) -> None:
        # a complete install (shim jar + libraries/ beside it) must not re-run
        # --installServer; only --force opts back in.
        http = FakeHttp()
        http.json_responses[PROMOTIONS_URL] = {"promos": {"1.20.4-latest": "49.2.8"}}
        paths = PathLayout(root=str(tmp_path), version="1.20.4", game_type="forge")
        process = FakeProcess()
        prepare = _server_prepare(tmp_path, http=http, process=process, game_type="forge")
        prepare.fs.ensure_dir(paths.server_dir())
        prepare.fs.write_text(paths.server_jar_path(), "fake")
        prepare.fs.ensure_dir(os.path.join(paths.server_dir(), "libraries"))
        provider = CoreProvider.for_type(GameType.FORGE)
        assert provider is not None
        provider.obtain(prepare)
        assert process.last_cmd is None  # installer never ran
        assert http.requests == []  # no promotions / installer download

    def test_paper_api_prefers_server_default_url(self, tmp_path) -> None:
        # Fill API: the concrete download url comes straight from the latest
        # build response; server:default wins over server:mojang when both exist.
        http = FakeHttp()
        # versions are grouped by major key, concrete newest-first
        http.json_responses = {
            "/v3/projects/paper": {"versions": {"26.2": ["26.2", "26.2-rc-2"], "1.20": ["1.20.6", "1.20.4", "1.20.2"]}},
            "/v3/projects/paper/versions/1.20.4/builds/latest": {
                "id": 499,
                "downloads": {
                    "server:mojang": {
                        "name": "paper-1.20.4-499-mojang.jar",
                        "url": "https://fill-data.papermc.io/v1/objects/mojang/paper-1.20.4-499-mojang.jar",
                    },
                    "server:default": {
                        "name": "paper-1.20.4-499.jar",
                        "url": "https://fill-data.papermc.io/v1/objects/abc/paper-1.20.4-499.jar",
                    },
                },
            },
        }
        assert (
            PaperAPI(_cache(tmp_path, http)).download_url("1.20.4")
            == "https://fill-data.papermc.io/v1/objects/abc/paper-1.20.4-499.jar"
        )

    def test_paper_api_major_group_uses_newest_concrete(self, tmp_path) -> None:
        # a group-level request ("1.20") resolves to the newest concrete within it
        http = FakeHttp()
        http.json_responses = {
            "/v3/projects/paper": {"versions": {"26.2": ["26.2", "26.2-rc-2"], "1.20": ["1.20.6", "1.20.4", "1.20.2"]}},
            "/v3/projects/paper/versions/1.20.6/builds/latest": {
                "id": 506,
                "downloads": {
                    "server:default": {
                        "name": "paper-1.20.6-506.jar",
                        "url": "https://fill-data.papermc.io/v1/objects/d/paper-1.20.6-506.jar",
                    }
                },
            },
        }
        assert (
            PaperAPI(_cache(tmp_path, http)).download_url("1.20")
            == "https://fill-data.papermc.io/v1/objects/d/paper-1.20.6-506.jar"
        )

    def test_paper_server_uses_fill_api_latest_build(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            "/v3/projects/paper": {"versions": {"26.2": ["26.2"], "1.20": ["1.20.6", "1.20.4", "1.20.2"]}},
            "/v3/projects/paper/versions/1.20.4/builds/latest": {
                "id": 499,
                "downloads": {
                    "server:default": {
                        "name": "paper-1.20.4-499.jar",
                        "url": "https://fill-data.papermc.io/v1/objects/abc/paper-1.20.4-499.jar",
                    }
                },
            },
        }
        prepare = _server_prepare(tmp_path, http=http, process=FakeProcess(), game_type="paper")
        provider = CoreProvider.for_type(GameType.PAPER)
        assert provider is not None
        provider.obtain(prepare)
        assert prepare.fs.is_file(prepare.paths.server_jar_path())

    def test_paper_server_unknown_version_raises(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"/v3/projects/paper": {"versions": {"26.2": ["26.2"]}}}
        prepare = _server_prepare(tmp_path, http=http, process=FakeProcess(), game_type="paper", version="1.21")
        provider = CoreProvider.for_type(GameType.PAPER)
        assert provider is not None
        with pytest_raises(RuntimeError, match="Paper 不支持 Minecraft 1.21"):
            provider.obtain(prepare)


class TestClientService:
    def test_build_classpath(self, tmp_path, reporter, sink, http) -> None:
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        fs = services.fs
        fs.write_text(paths.client_jar_path(), "jar")
        lib = resolve_libraries({"libraries": [{"name": "com.example:lib1:1.0"}]}, os_name="linux")[0]
        fs.write_text(paths.client_library_path(lib.path), "lib")

        client = ClientService(services)
        classpath = client._build_classpath(
            {"libraries": [{"name": "com.example:lib1:1.0"}], "arguments": {"game": [], "jvm": []}}, None
        )
        assert paths.client_jar_path() in classpath
        assert paths.client_library_path(lib.path) in classpath

    def test_build_classpath_includes_native_jars(self, tmp_path, reporter, sink, http) -> None:
        # Modern Mojang format: native libraries (classifier embedded in the
        # coordinates, e.g. org.lwjgl:lwjgl:3.4.1:natives-macos-arm64) must stay on
        # the classpath — the game self-extracts them from there. Excluding them is
        # what crashed the client with "Failed to locate library: liblwjgl.dylib".
        # The rule mirrors the current platform so the assertion holds on every OS
        # (on Linux a mac-only native is correctly filtered out by the rules).
        services = _services(tmp_path, reporter, sink, http)
        paths = services.context.paths
        fs = services.fs
        os_name = os_key()
        classifier = f"natives-{os_name}-{os_arch()}"
        rel_path = f"org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1-{classifier}.jar"
        native: dict = {
            "name": f"org.lwjgl:lwjgl:3.4.1:{classifier}",
            "rules": [{"action": "allow", "os": {"name": os_name}}],
            "downloads": {"artifact": {"path": rel_path, "url": "https://libraries.minecraft.net/" + rel_path}},
        }
        fs.write_text(paths.client_jar_path(), "jar")
        native_path = paths.client_library_path(rel_path)
        fs.write_text(native_path, "jar")

        client = ClientService(services)
        classpath = client._build_classpath({"libraries": [native]}, None)
        assert native_path in classpath

    def test_run_requires_version(self, tmp_path, reporter, sink, http) -> None:
        # a client run on an empty root must raise a clear error, not hang on the network
        services = Services(
            RuntimeOptions(root_dir=str(tmp_path), version="1.20.4"),
            reporter=reporter,
            sink=sink,
            http=http,
            fs=FileStore(),
        )
        with pytest_raises(RuntimeError):
            ClientService(services).run()


def _make_tar_gz(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_archive(files: dict[str, str]) -> bytes:
    """Canned Adoptium archive in the current platform's format: Windows ships
    .zip, macOS/Linux .tar.gz, and JavaEnv._extract keys off the extension."""
    if sys.platform == "win32":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for path, content in files.items():
                zf.writestr(path, content)
        return buf.getvalue()
    return _make_tar_gz(files)


def _java_exe() -> str:
    """Platform java executable name — Windows ships java.exe (java_bin adds
    the suffix), macOS/Linux ship plain java."""
    return "java.exe" if sys.platform == "win32" else "java"


def pytest_raises(exc, **kwargs):
    from pytest import raises

    return raises(exc, **kwargs)
