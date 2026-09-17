"""AppContext + Services: the composed dependency graph for one invocation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from orzmc.core.mojang import Mojang
from orzmc.domain.options import RuntimeOptions
from orzmc.domain.paths import DEFAULT_ROOT, PathLayout
from orzmc.domain.types import GameType
from orzmc.infra.cache import MetadataCache
from orzmc.infra.fs import FileStore
from orzmc.infra.http import DEFAULT_POOL_SIZE, HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.progress import NullProgress, ProgressSink
from orzmc.infra.runner import ProcessRunner
from orzmc.services.downloader import Downloader
from orzmc.services.java import JavaEnv


@dataclass(frozen=True)
class AppContext:
    """The bound value objects for one launch/deploy operation."""

    options: RuntimeOptions
    paths: PathLayout

    @classmethod
    def build(cls, options: RuntimeOptions) -> AppContext:
        paths = PathLayout(
            root=options.root_dir or DEFAULT_ROOT,
            version=options.version,
            game_type=options.game_type,
        )
        return cls(options=options, paths=paths)

    @property
    def game_type(self) -> GameType:
        return self.options.game_type_obj


class Services:
    """Everything a service needs, wired for one (version-bound) invocation."""

    def __init__(
        self,
        options: RuntimeOptions,
        reporter: Reporter | None = None,
        sink: ProgressSink | None = None,
        http: HttpClient | None = None,
        fs: FileStore | None = None,
    ) -> None:
        self.options = options
        self.reporter = reporter or NullReporter()
        self.sink = sink or NullProgress()
        # keep-alive 池必须 ≥ 下载并发数:否则 urllib3 会丢弃超限连接、每个请求重新
        # TCP+TLS。默认池已经够 32 并发,用户把并发调得更高时跟着放大。
        self.http = http or HttpClient(pool_size=max(DEFAULT_POOL_SIZE, options.download_threads))
        self.fs = fs or FileStore()
        self.process = ProcessRunner(self.reporter)
        self.context = AppContext.build(options)
        paths = self.context.paths
        self.downloader = Downloader(
            self.http, self.fs, self.reporter, self.sink, paths, workers=options.download_threads
        )
        self.java_env = JavaEnv(self.http, self.fs, self.reporter, self.sink, paths)
        # One metadata cache per invocation: it carries the library-wide TTL and
        # the CLI ``--refresh`` flag, so every remote API (Mojang / Fabric /
        # Paper / Forge) shares one refresh + TTL policy.
        self.cache = MetadataCache(
            self.http,
            self.fs,
            paths.cache_dir(),
            refresh=options.refresh,
            reporter=self.reporter,
            sink=self.sink,
        )
        self.mojang = Mojang(self.cache, paths.version_manifest_path(), paths.version_jsons_dir())

    def resolve_java(self, major: int, confirm: Callable[[int, bool], bool] | None = None) -> str:
        """Java binary for a version JSON's required major — shared by client & server.

        A sandboxed runtime suffices for every supported type: no game type
        needs a full JDK at run time (install/build steps use ``need_jdk``
        explicitly only if a provider ever requires it).
        """
        return self.java_env.resolve(major, confirm=confirm)

    def for_version(self, version: str) -> Services:
        """A copy of the services bound to a concrete Minecraft version."""
        return Services(
            replace(self.options, version=version),
            reporter=self.reporter,
            sink=self.sink,
            http=self.http,
            fs=self.fs,
        )
