"""Runtime options for a single client / server operation (pure data, no IO)."""

from __future__ import annotations

from dataclasses import dataclass

from orzmc.domain.types import GameType

# 下载并发数。资源阶段是「几千个 <50KB 的小文件」,耗时几乎完全由「每请求延迟 乘 并发数」
# 决定而不是带宽:实测同一台机器上 8 线程 8.7 req/s、16 线程 15.8、24 线程 20.3、
# 32 线程 23.3(再往上中位延迟明显上升、收益递减)。默认取 16:比原来的 8 快约 1.8 倍,
# 同时对上游 CDN 足够克制;嫌慢可以用 ``--download-threads`` 自己调。
DEFAULT_DOWNLOAD_THREADS = 16
MAX_DOWNLOAD_THREADS = 64


@dataclass(frozen=True)
class RuntimeOptions:
    """Parsed CLI options that drive one launch or deployment.

    No IO, no side effects — a plain value object consumed by services.
    """

    is_client: bool = True
    version: str | None = None
    username: str = "guest"
    game_type: str = GameType.VANILLA.value
    min_mem: str = "512M"
    max_mem: str = "2G"
    extract_music: bool = False
    force_upgrade: bool = False
    force_download: bool = False
    # ``refresh`` bypasses the metadata cache (see infra.cache); CLI self-upgrade
    # is a separate command (``orzmc update``), never a launch/deploy option.
    refresh: bool = False
    # Concurrent file downloads. The HTTP keep-alive pool is sized from this
    # (see Services), so raising it never costs extra TLS handshakes.
    download_threads: int = DEFAULT_DOWNLOAD_THREADS
    symlink: bool = False
    jvm_opts: str | None = None
    server_args: str | None = None
    nogui: bool = False
    yes: bool = False
    root_dir: str | None = None

    @property
    def game_type_obj(self) -> GameType:
        return GameType.parse(self.game_type)
