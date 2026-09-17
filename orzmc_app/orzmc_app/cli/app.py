"""Typer CLI: subcommands that call only the orzmc library public API.

``orzmc`` with no subcommand prints help; every subcommand works as a plain
command line, with lightweight rich prompts when run interactively (version
defaults to Mojang latest when neither ``-v`` nor a TTY prompt is available).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from orzmc import (
    GameType,
    RuntimeOptions,
    backup_world,
    check_self_update,
    deploy_server,
    launch_client,
    list_versions,
    remove_version,
    uninstall_self,
    update_self,
)
from orzmc import (
    __version__ as LIB_VERSION,
)
from orzmc.infra.log import RichReporter
from orzmc.infra.progress import RichProgress
from orzmc_app import __version__ as APP_VERSION
from orzmc_app.cli.options import JvmOpts, MaxMem, MinMem, Refresh, RootDir, Username, Verbose, Version, Yes
from orzmc_app.cli.prompts import confirm_eula, confirm_java, is_interactive, resolve_username, resolve_version

_console = Console(highlight=False)
app = typer.Typer(add_completion=False, no_args_is_help=False, invoke_without_command=True)

_MEM_RE = re.compile(r"^\d+[kKmMgGtT]$")


def _fail(message: str) -> NoReturn:
    _console.print(f"[error]错误:[/error] {message}")
    raise typer.Exit(1)


def _check_mem(name: str, value: str) -> None:
    if not _MEM_RE.match(value):
        _fail(f"无效内存: {name}={value} (例如 512M、2G)")


def _parse_type(value: str, *, client: bool) -> GameType:
    try:
        game_type = GameType.parse(value)
    except ValueError:
        _fail(f"未知类型: {value}")
    ok = game_type.is_client_capable if client else game_type.is_server_capable
    if not ok:
        _fail(f"类型 {value} 不能用于{'客户端' if client else '服务端'}")
    return game_type


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context, verbose: Verbose = False) -> None:
    """OrzMC — Minecraft 客户端启动 / 服务端部署工具。

    不带子命令时打印帮助;直接使用子命令(如 ``orzmc client -v 26.2``)。
    """
    ctx.obj = {"verbose": verbose}
    if ctx.invoked_subcommand is None:
        _console.print(ctx.get_help(), markup=False)
        raise typer.Exit(0)


@app.command()
def client(
    ctx: typer.Context,
    version: Version = None,
    username: Username = None,
    game_type: Annotated[str, typer.Option("--type", "-t", help="类型: vanilla|fabric|forge")] = "vanilla",
    min_mem: MinMem = "512M",
    max_mem: MaxMem = "2G",
    extract_music: Annotated[bool, typer.Option("--extract-music", help="提取客户端音乐后退出")] = False,
    jvm_opts: JvmOpts = None,
    refresh: Refresh = False,
    root_dir: RootDir = None,
) -> None:
    """运行 Minecraft 客户端(缺失文件自动下载即安装)。"""
    game_type_obj = _parse_type(game_type, client=True)
    _check_mem("min", min_mem)
    _check_mem("max", max_mem)
    resolved = resolve_version(version, root_dir, refresh=refresh)
    username = resolve_username(username)
    options = RuntimeOptions(
        is_client=True,
        version=resolved,
        username=username,
        game_type=game_type_obj.value,
        min_mem=min_mem,
        max_mem=max_mem,
        extract_music=extract_music,
        jvm_opts=jvm_opts,
        refresh=refresh,
        root_dir=root_dir,
    )
    reporter = RichReporter(verbose=bool(ctx.obj.get("verbose")))
    sink = RichProgress()
    try:
        launch_client(options, reporter=reporter, sink=sink, confirm_java=confirm_java if is_interactive() else None)
    except Exception as exc:
        _fail(str(exc))
    finally:
        sink.close()


@app.command()
def server(
    ctx: typer.Context,
    version: Version = None,
    game_type: Annotated[str, typer.Option("--type", "-t", help="类型: vanilla|paper|fabric|forge")] = "vanilla",
    min_mem: MinMem = "512M",
    max_mem: MaxMem = "2G",
    force_upgrade: Annotated[bool, typer.Option("--force-upgrade", help="服务端启动加 --forceUpgrade")] = False,
    symlink: Annotated[bool, typer.Option("--symlink", help="软链接到备份世界")] = False,
    force_download: Annotated[bool, typer.Option("--force-download", help="强制重新下载核心")] = False,
    yes: Yes = False,
    jvm_opts: JvmOpts = None,
    server_args: Annotated[str | None, typer.Option("--server-args", help="服务端程序参数(如 '--port 25565')")] = None,
    nogui: Annotated[
        bool, typer.Option("--nogui", help="无窗口模式启动(不弹服务端 GUI);终端输入 stop 或 Ctrl-C 关闭")
    ] = False,
    refresh: Refresh = False,
    root_dir: RootDir = None,
) -> None:
    """部署并运行 Minecraft 服务端(缺失文件自动下载即安装)。"""
    game_type_obj = _parse_type(game_type, client=False)
    _check_mem("min", min_mem)
    _check_mem("max", max_mem)
    resolved = resolve_version(version, root_dir, refresh=refresh)
    options = RuntimeOptions(
        is_client=False,
        version=resolved,
        game_type=game_type_obj.value,
        min_mem=min_mem,
        max_mem=max_mem,
        force_upgrade=force_upgrade,
        symlink=symlink,
        force_download=force_download,
        yes=yes,
        jvm_opts=jvm_opts,
        server_args=server_args,
        nogui=nogui,
        refresh=refresh,
        root_dir=root_dir,
    )
    reporter = RichReporter(verbose=bool(ctx.obj.get("verbose")))
    sink = RichProgress()
    try:
        deploy_server(
            options,
            reporter=reporter,
            sink=sink,
            confirm_java=confirm_java if is_interactive() else None,
            confirm_eula=confirm_eula if is_interactive() else None,
        )
    except Exception as exc:
        _fail(str(exc))
    finally:
        sink.close()


@app.command()
def remove(
    version: Annotated[str, typer.Option("--version", "-v", help="要移除的 Minecraft 版本")],
    server: Annotated[bool, typer.Option("--server", help="移除服务端(默认移除客户端)")] = False,
    game_type: Annotated[str | None, typer.Option("--type", "-t", help="服务端类型(移除服务端时必填)")] = None,
    yes: Yes = False,
    root_dir: RootDir = None,
) -> None:
    """移除已安装的版本(客户端或某类型的服务端)。"""
    if server and not game_type:
        _fail("移除服务端需要指定类型 (-t)")
    try:
        removed = remove_version(
            version,
            is_client=not server,
            game_type=game_type,
            root_dir=root_dir,
            yes=yes,
            # 非交互时无法确认,视为取消(不删除);交互时弹 rich 确认。
            confirm=(
                (lambda desc: bool(Confirm.ask(f"{desc}\n确定移除吗?", default=False)))
                if is_interactive()
                else (lambda desc: False)
            ),
        )
    except Exception as exc:
        _fail(str(exc))
    if removed:
        _console.print(f"[success]已移除 {version}[/success]")
    else:
        _console.print("[yellow]已取消移除[/yellow]")


@app.command("self-uninstall")
def self_uninstall(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", help="跳过确认(游戏数据默认保留)")] = False,
    remove_root: Annotated[
        bool, typer.Option("--remove-root", help="连游戏数据根目录(默认 ~/minecraft)一起删除")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="绕过安全护栏(用于开发环境安装)")] = False,
    root_dir: RootDir = None,
) -> None:
    """卸载工具本身:删除二进制、还原 PATH、可选删除游戏数据。

    与 ``remove``(移除 Minecraft 版本)不同,``self-uninstall`` 删除的是
    ``orzmc`` 自身。游戏数据默认保留;``--remove-root`` 连 ``~/minecraft`` 一起删。
    """
    binary = str(Path(sys.argv[0]).resolve())
    try:
        uninstall_self(
            binary,
            root_dir=root_dir,
            remove_root=remove_root,
            yes=yes,
            force=force,
            reporter=RichReporter(verbose=bool(ctx.obj.get("verbose"))),
            # 非交互时无法确认,交给库默认保留游戏数据;交互时弹 rich 确认。
            confirm=(
                (lambda desc: bool(Confirm.ask(f"{desc}\n确定删除吗?", default=False))) if is_interactive() else None
            ),
        )
    except Exception as exc:
        _fail(str(exc))


@app.command()
def update(
    ctx: typer.Context,
    version: Annotated[
        str | None, typer.Option("--version", "-v", help="升级到指定版本(如 v2.1.0);缺省用 GitHub 最新发布")
    ] = None,
    check: Annotated[bool, typer.Option("--check", help="只检查是否有新版本,不下载")] = False,
    file: Annotated[str | None, typer.Option("--file", help="用本地二进制升级(需同时 --version;离线/测试接缝)")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="跳过升级确认")] = False,
    force: Annotated[bool, typer.Option("--force", help="绕过开发环境护栏(pip / venv 安装仍会被拒绝)")] = False,
) -> None:
    """升级工具本身(orzmc 二进制),不是 Minecraft 版本。

    不带参数时查询 GitHub 最新发布并覆盖当前二进制;``-v/--version`` 指定版本
    (也是 GitHub API 限流时的回退),``--check`` 只检查,``--file`` 用本地文件。
    卸载请用 ``self-uninstall``,Minecraft 版本管理请用 ``list`` / ``remove``。
    """
    binary = str(Path(sys.argv[0]).resolve())
    reporter = RichReporter(verbose=bool(ctx.obj.get("verbose")))
    if check:
        try:
            current = check_self_update(binary, version=version, reporter=reporter)
        except Exception as exc:
            _fail(str(exc))
        _console.print(f"当前版本: [bold]{current.current}[/bold]")
        _console.print(f"最新版本: [bold]{current.latest}[/bold]")
        if current.available:
            _console.print("[yellow]有新版本可用:orzmc update[/yellow]")
        else:
            _console.print("[success]已是最新版本[/success]")
        return
    sink = RichProgress()
    try:
        applied = update_self(
            binary,
            version=version,
            file=file,
            yes=yes,
            force=force,
            reporter=reporter,
            sink=sink,
            # 非交互(脚本/管道)时直接执行 —— 用户已显式调用 update;交互时弹确认。
            confirm=(
                (lambda desc: bool(Confirm.ask(f"{desc}\n确定升级吗?", default=True))) if is_interactive() else None
            ),
        )
    except Exception as exc:
        _fail(str(exc))
    finally:
        sink.close()
    if applied is None:
        _console.print("[yellow]已取消升级[/yellow]")


@app.command()
def list(root_dir: RootDir = None) -> None:
    """列出已安装的版本(客户端 / 服务端类型)。"""
    items = list_versions(root_dir=root_dir)
    if not items:
        _console.print("[muted]尚未安装任何版本[/muted]")
        return
    table = Table(title="已安装版本", header_style="bold")
    table.add_column("版本")
    table.add_column("客户端", justify="center")
    table.add_column("服务端")
    for item in items:
        table.add_row(item.version, "✓" if item.has_client else "", ", ".join(item.server_types))
    _console.print(table)


@app.command()
def backup(
    version: Annotated[str | None, typer.Option("--version", "-v", help="要备份的版本(缺省自动推断)")] = None,
    game_type: Annotated[str, typer.Option("--type", "-t", help="服务端类型")] = "vanilla",
    root_dir: RootDir = None,
) -> None:
    """备份服务端世界到 ``backup/worlds/``。"""
    resolved = _infer_backup_version(version, root_dir)
    try:
        dest = backup_world(resolved, game_type=game_type, root_dir=root_dir)
    except Exception as exc:
        _fail(str(exc))
    _console.print(f"[success]备份完成:[/success] {dest}")


@app.command()
def version() -> None:
    """打印工具版本。"""
    _console.print(f"orzmc {APP_VERSION} (库 {LIB_VERSION})")


def _infer_backup_version(version: str | None, root_dir: str | None) -> str:
    if version:
        return version
    installed = list_versions(root_dir=root_dir)
    candidates = [item.version for item in installed if item.server_types]
    if not candidates:
        _fail("没有已安装的服务端,请用 -v 指定版本")
    if len(candidates) == 1:
        return candidates[0]
    if is_interactive():
        _console.print("[info]可备份的服务端版本:[/info]")
        for i, item in enumerate(candidates, 1):
            _console.print(f"  [muted]{i:>2}.[/muted] {item}")
        from rich.prompt import Prompt

        picked = Prompt.ask("选择版本", default="")
        if picked.isdigit() and 1 <= int(picked) <= len(candidates):
            return candidates[int(picked) - 1]
        return picked or candidates[0]
    _fail("有多个服务端版本,请用 -v 指定")
