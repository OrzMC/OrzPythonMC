"""Shared typer option declarations reused across CLI subcommands."""

from __future__ import annotations

from typing import Annotated

import typer

from orzmc import DEFAULT_DOWNLOAD_THREADS, MAX_DOWNLOAD_THREADS

# ── generic ──────────────────────────────────────────────────────────────────


def _root_dir_help() -> str:
    return "游戏根目录(默认 ~/minecraft)"


def _version_help() -> str:
    return "Minecraft 版本(缺省:TTY 交互选择,可搜索/切换正式版·测试版;非 TTY 用 Mojang 最新 release)"


def _username_help() -> str:
    return "游戏用户名(缺省 TTY 交互提示,回车用默认 guest;非 TTY 用默认 guest)"


# ── option type aliases ──────────────────────────────────────────────────────

RootDir = Annotated[str | None, typer.Option("--root-dir", help=_root_dir_help())]
Version = Annotated[str | None, typer.Option("--version", "-v", help=_version_help())]
Username = Annotated[str | None, typer.Option("--username", "-u", help=_username_help())]
Refresh = Annotated[
    bool,
    typer.Option("--refresh", help="忽略元数据缓存(默认 24 小时内复用),强制重新拉取版本清单与类型元数据"),
]
DownloadThreads = Annotated[
    int,
    typer.Option(
        "--download-threads",
        "-j",
        help=(
            f"下载并发数(1-{MAX_DOWNLOAD_THREADS},默认 {DEFAULT_DOWNLOAD_THREADS})。"
            "资源阶段是几千个小文件,延迟高/代理链路上调大明显更快"
        ),
    ),
]
MinMem = Annotated[str, typer.Option("--minmem", "-m", help="最小内存(如 512M)")]
MaxMem = Annotated[str, typer.Option("--maxmem", "-x", help="最大内存(如 2G)")]
JvmOpts = Annotated[str | None, typer.Option("--jvm-opts", help="附加 JVM 旗标(空格分隔,如 '-XX:+UseZGC')")]
Verbose = Annotated[bool, typer.Option("--verbose", help="输出调试日志")]
Yes = Annotated[bool, typer.Option("--yes", help="跳过交互确认(自动接受 EULA 等)")]
