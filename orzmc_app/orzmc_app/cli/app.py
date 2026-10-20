"""Typer CLI: subcommands that call only the orzmc library public API.

``orzmc`` with no subcommand prints help; every subcommand works as a plain
command line, with lightweight rich prompts when run interactively (version
defaults to Mojang latest when neither ``-v`` nor a TTY prompt is available).
"""

from __future__ import annotations

import json
import re
import sys
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from typer.completion import get_completion_script

from orzmc import (
    DEFAULT_DOWNLOAD_THREADS,
    MAX_DOWNLOAD_THREADS,
    AppInstaller,
    AuthError,
    AuthSession,
    GameType,
    PluginService,
    RuntimeOptions,
    VersionNotFoundError,
    authenticate,
    backup_world,
    check_self_update,
    deploy_server,
    disclaimer_text,
    kill_all_servers,
    launch_client,
    list_versions,
    record_completion_file,
    remove_version,
    resolve_client_id,
    running_binary,
    uninstall_self,
    update_self,
)
from orzmc import (
    __version__ as LIB_VERSION,
)
from orzmc.infra.log import RichReporter
from orzmc.infra.progress import RichProgress
from orzmc_app import __version__ as APP_VERSION
from orzmc_app.cli.completions import SHELLS as INSTALLABLE_SHELLS
from orzmc_app.cli.completions import UnsupportedInstall, resolve_shell
from orzmc_app.cli.completions import install as install_completion
from orzmc_app.cli.introspect import command_specs
from orzmc_app.cli.options import (
    DownloadThreads,
    JvmOpts,
    MaxMem,
    MinMem,
    Refresh,
    RootDir,
    Username,
    Verbose,
    Version,
    Yes,
)
from orzmc_app.cli.prompts import confirm_eula, confirm_java, is_interactive, resolve_username, resolve_version

_console = Console(highlight=False)
# 规范 §6:错误信息写 **stderr**(人类可读结果才进 stdout)。
_console_err = Console(stderr=True, highlight=False)

# 非 macOS 上 ``orzmc app`` 的固定报错(规范 install-update.md §8;与 Swift 侧逐字一致)。
_APP_UNSUPPORTED = "「orzmc app」仅支持 macOS;当前平台不支持,请从 Release 下载应用包"

# ``-h`` 是规范 §3 / ``cli-surface.json`` 的 ``globalOptions`` 要求(两套实现都要有);
# click 的 ``help_option_names`` 会被子 context 继承,故根上写一次即可。
app = typer.Typer(
    add_completion=True,
    no_args_is_help=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

# typer 的 ``add_completion=True`` 干了两件事:(1) 注册补全类 —— click 处理
# ``_ORZMC_COMPLETE`` 环境变量(= ``orzmc completion <shell>`` 生成的脚本所依赖的
# 动态补全)必须有它;(2) 往根命令塞 ``--install-completion`` / ``--show-completion``
# —— 这两个与我们按规范 §9 实现的 ``completion`` 子命令重复、英文文案、且**只能靠
# shellingham 探测 shell**(冻结二进制里直接 RuntimeError)。
# 所以:先触发一次注册,再关掉 typer 私有开关把这两个选项摘掉。
# ``test_typer_completion_options_are_not_exposed`` 守着这个不变式,typer 升级把它
# 改坏时会在单测里红,而不是等发布后由用户踩。
typer.main.get_install_completion_arguments()
app._add_completion = False

_MEM_RE = re.compile(r"^\d+[kKmMgGtT]$")

# 规范 §9 支持的补全 shell(与 capabilities 上报的 completion.target 枚举同源)。
_COMPLETION_SHELLS = frozenset(INSTALLABLE_SHELLS)

# 当前命令名 + 是否 `--json`(单一进程内单次调用;每次命令开头用 `_begin` 重设)。
_STATE: dict[str, object] = {"json": False, "command": "_root"}


def _begin(command: str, ctx: typer.Context) -> None:
    """记录当前命令与 `--json` 模式,供 `_fail` / `_emit` 使用。"""
    _STATE["command"] = command
    _STATE["json"] = bool((ctx.obj or {}).get("json"))


def _json_mode() -> bool:
    return bool(_STATE["json"])


def _print_json(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _fail(message: str, *, code: str = "E_USAGE") -> NoReturn:
    if _json_mode():
        _print_json(
            {
                "ok": False,
                "command": _STATE["command"],
                "data": None,
                "error": {"code": code, "message": message},
            }
        )
    else:
        _console_err.print(f"[error]错误:[/error] {message}")
    raise typer.Exit(130 if code == "E_CANCELLED" else 1)


def _emit(data: object) -> None:
    """成功结果:`--json` 时输出信封(人类可读分支由调用方自己跳过)。"""
    if _json_mode():
        _print_json({"ok": True, "command": _STATE["command"], "data": data, "error": None})


def _reporter(ctx: typer.Context) -> RichReporter:
    """日志:`--json` 时写 stderr(stdout 只留信封),verbose 来自根回调。"""
    console = Console(stderr=True) if _json_mode() else None
    return RichReporter(console=console, verbose=bool((ctx.obj or {}).get("verbose")))


def _progress() -> RichProgress:
    """进度:`--json` 时写 stderr。"""
    return RichProgress(console=Console(stderr=True) if _json_mode() else None)


def _check_mem(name: str, value: str) -> None:
    if not _MEM_RE.match(value):
        _fail(f"无效内存: {name}={value} (例如 512M、2G)")


def _check_threads(value: int) -> int:
    """Validate the download concurrency; typer only enforces the type."""
    if not 1 <= value <= MAX_DOWNLOAD_THREADS:
        _fail(f"下载并发数必须在 1-{MAX_DOWNLOAD_THREADS} 之间(当前 {value})")
    return value


def _parse_type(value: str, *, client: bool) -> GameType:
    try:
        game_type = GameType.parse(value)
    except ValueError:
        _fail(f"未知类型: {value}")
    ok = game_type.is_client_capable if client else game_type.is_server_capable
    if not ok:
        _fail(f"类型 {value} 不能用于{'客户端' if client else '服务端'}", code="E_UNSUPPORTED_TYPE")
    return game_type


def _show_device_code(device: Any) -> None:
    """Show device-code instructions on stderr (safe for --json / non-TTY)."""
    sys.stderr.write(f"请在浏览器打开 {device.verification_uri} 并输入代码 {device.user_code}\n")
    if device.message:
        sys.stderr.write(f"{device.message}\n")
    sys.stderr.flush()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    verbose: Verbose = False,
    root_dir: RootDir = None,
    json_output: Annotated[bool, typer.Option("--json", help="机器可读输出(信封见规范 §6.1)")] = False,
    version: Annotated[bool, typer.Option("--version", help="打印工具版本并退出")] = False,
) -> None:
    """OrzMC — Minecraft 客户端启动 / 服务端部署工具。

    不带子命令时打印帮助;直接使用子命令(如 ``orzmc client -v 26.2``)。
    """
    if version:
        if json_output:
            _print_json({"tool": APP_VERSION, "library": LIB_VERSION, "disclaimer": disclaimer_text()})
        else:
            _console.print(f"orzmc {APP_VERSION} (库 {LIB_VERSION})")
            _console.print()
            _console.print(disclaimer_text(), style="dim", soft_wrap=True)
        raise typer.Exit(0)
    ctx.obj = {"verbose": verbose, "root_dir": root_dir, "json": json_output}
    _begin("_root", ctx)
    if ctx.invoked_subcommand is None:
        if _json_mode():
            # `--json` 承诺 stdout 只有信封,帮助是给人看的 —— 与 Swift 一致:
            # 缺子命令按用法错误处理(§1 的「打印帮助并退 0」只在人类可读模式下成立)。
            _fail("缺少子命令(用 orzmc --help 查看全部命令)", code="E_USAGE")
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
    download_threads: DownloadThreads = DEFAULT_DOWNLOAD_THREADS,
    root_dir: RootDir = None,
    auth: Annotated[
        bool,
        typer.Option(
            "--auth",
            help="正版授权(Microsoft 设备码流程;client_id 内置,可用 ORZMC_AZURE_CLIENT_ID 覆盖)",
        ),
    ] = False,
) -> None:
    """运行 Minecraft 客户端(缺失文件自动下载即安装)。"""
    _begin("client", ctx)
    root_dir = root_dir or ctx.obj.get("root_dir")
    game_type_obj = _parse_type(game_type, client=True)
    _check_mem("min", min_mem)
    _check_mem("max", max_mem)
    download_threads = _check_threads(download_threads)

    # 正版授权(可选):设备码 → Xbox/XSTS → MC 登录 → 档案;结果贯穿启动参数。
    session: AuthSession | None = None
    if auth:
        client_id = resolve_client_id()
        if not client_id:
            _fail(
                "未配置 Azure 应用 client_id(设置 ORZMC_AZURE_CLIENT_ID);见 docs/spec/auth.md",
                code="E_AUTH_NOT_CONFIGURED",
            )
        try:
            session = authenticate(client_id, reporter=_reporter(ctx), on_device_code=_show_device_code)
        except AuthError as exc:
            _fail(exc.message, code=exc.code)

    resolved = resolve_version(version, root_dir, refresh=refresh)
    # 认证成功以档案用户名为准(覆盖 -u);否则走交互/默认。
    username = session.name if session is not None else resolve_username(username)
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
        download_threads=download_threads,
        root_dir=root_dir,
        auth_uuid=session.uuid if session is not None else None,
        auth_access_token=session.access_token if session is not None else None,
        auth_user_type="msa" if session is not None else "legacy",
    )
    reporter = _reporter(ctx)
    sink = _progress()
    try:
        code = launch_client(
            options,
            reporter=reporter,
            sink=sink,
            confirm_java=confirm_java if is_interactive() else None,
        )
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    finally:
        sink.close()
    result: dict[str, Any] = {"version": resolved, "username": username, "exitCode": code}
    if session is not None:
        result.update(
            {
                "authenticated": True,
                "uuid": session.uuid,
                "expiresAt": session.expires_at,
                "source": session.source,
            }
        )
    _emit(result)


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
    demo: Annotated[bool, typer.Option("--demo", help="演示模式启动(服务端加 --demo)")] = False,
    print_jar_help: Annotated[
        bool, typer.Option("--print-jar-help", help="打印服务端 jar 的帮助信息(服务端加 --help)")
    ] = False,
    online_mode: Annotated[
        bool, typer.Option("--online-mode", "-o", help="正版验证(写入 server.properties online-mode)")
    ] = False,
    refresh: Refresh = False,
    download_threads: DownloadThreads = DEFAULT_DOWNLOAD_THREADS,
    kill_all: Annotated[bool, typer.Option("--kill-all", "-k", help="杀掉所有运行中的服务端")] = False,
    root_dir: RootDir = None,
) -> None:
    """部署并运行 Minecraft 服务端(缺失文件自动下载即安装)。"""
    _begin("server", ctx)
    root_dir = root_dir or ctx.obj.get("root_dir")
    if kill_all:
        try:
            killed = kill_all_servers(root_dir=root_dir)
        except Exception as exc:
            _fail(str(exc), code="E_RUNTIME")
        _emit({"mode": "kill-all", "killed": killed})
        if not _json_mode():
            if killed:
                _console.print(f"[success]已终止 {len(killed)} 个服务端进程[/success]")
            else:
                _console.print("没有运行中的服务端")
        return
    game_type_obj = _parse_type(game_type, client=False)
    _check_mem("min", min_mem)
    _check_mem("max", max_mem)
    download_threads = _check_threads(download_threads)
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
        demo=demo,
        print_jar_help=print_jar_help,
        online_mode=online_mode,
        refresh=refresh,
        download_threads=download_threads,
        root_dir=root_dir,
    )
    reporter = _reporter(ctx)
    sink = _progress()
    try:
        code = deploy_server(
            options,
            reporter=reporter,
            sink=sink,
            confirm_java=confirm_java if is_interactive() else None,
            confirm_eula=confirm_eula if is_interactive() else None,
        )
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    finally:
        sink.close()
    _emit({"version": resolved, "type": game_type_obj.value, "exitCode": code})


@app.command()
def remove(
    ctx: typer.Context,
    version: Annotated[str, typer.Option("--version", "-v", help="要移除的 Minecraft 版本")],
    server: Annotated[bool, typer.Option("--server", help="移除服务端(默认移除客户端)")] = False,
    game_type: Annotated[str | None, typer.Option("--type", "-t", help="服务端类型(移除服务端时必填)")] = None,
    yes: Yes = False,
    root_dir: RootDir = None,
) -> None:
    """移除已安装的版本(客户端或某类型的服务端)。"""
    _begin("remove", ctx)
    root_dir = root_dir or ctx.obj.get("root_dir")
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
    except VersionNotFoundError as exc:
        _fail(str(exc), code="E_NOT_FOUND")
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    if not removed:
        # 规范 §8.2:需要确认的命令在缺 ``--yes`` 时返回取消(退出 130)。
        # 之前这里打一行「已取消移除」并以 0 退出,``--json`` 还会报 ``ok: true``
        # —— AI / 脚本会当成「已删除」。
        _fail("已取消移除", code="E_CANCELLED")
    _emit({"version": version, "removed": removed})
    if not _json_mode():
        _console.print(f"[success]已移除 {version}[/success]")


@app.command("uninstall")
def uninstall(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", help="跳过确认(游戏数据默认保留)")] = False,
    remove_root: Annotated[
        bool, typer.Option("--remove-root", help="连游戏数据根目录(默认 ~/minecraft)一起删除")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="绕过安全护栏(用于开发环境安装)")] = False,
    root_dir: RootDir = None,
) -> None:
    """卸载工具本身:删除二进制、还原 PATH、可选删除游戏数据。

    与 ``remove``(移除 Minecraft 版本)不同,``uninstall`` 删除的是
    ``orzmc`` 自身。游戏数据默认保留;``--remove-root`` 连 ``~/minecraft`` 一起删。
    """
    _begin("uninstall", ctx)
    binary = running_binary()
    root_dir = root_dir or ctx.obj.get("root_dir")
    # 规范 ``frontends.md`` F2:卸载必须保留二次确认。非交互环境没有「二次确认」
    # 可言,故要求显式 ``--yes``(与 ``remove`` 同一策略);否则返回 130 而不是静默删掉。
    if not yes and not is_interactive():
        _fail("非交互环境卸载需显式 --yes(游戏数据默认保留)", code="E_CANCELLED")
    try:
        uninstalled = uninstall_self(
            binary,
            root_dir=root_dir,
            remove_root=remove_root,
            yes=yes,
            force=force,
            # 必须用 ``_reporter``:``--json`` 时它把日志切到 stderr,否则库里的
            # 「已卸载 orzmc」等行会写进 stdout,破坏「stdout 恰好一个 JSON 对象」。
            reporter=_reporter(ctx),
            # 非交互时无法确认,交给库默认保留游戏数据;交互时弹 rich 确认。
            confirm=(
                (lambda desc: bool(Confirm.ask(f"{desc}\n确定删除吗?", default=False))) if is_interactive() else None
            ),
        )
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    _emit({"uninstalled": uninstalled})


@app.command()
def update(
    ctx: typer.Context,
    version: Annotated[
        str | None, typer.Option("--version", "-v", help="升级到指定版本(如 v2.1.0);缺省用 GitHub 最新发布")
    ] = None,
    check: Annotated[bool, typer.Option("--check", help="只检查是否有新版本,不下载")] = False,
    channel: Annotated[str, typer.Option("--channel", help="更新渠道: release(默认)|beta")] = "release",
    file: Annotated[str | None, typer.Option("--file", help="用本地二进制升级(需同时 --version;离线/测试接缝)")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="跳过升级确认")] = False,
    force: Annotated[bool, typer.Option("--force", help="绕过开发环境护栏(pip / venv 安装仍会被拒绝)")] = False,
) -> None:
    """升级工具本身(orzmc 二进制),不是 Minecraft 版本。

    不带参数时查询 GitHub 最新发布并覆盖当前二进制;``-v/--version`` 指定版本
    (也是 GitHub API 限流时的回退),``--check`` 只检查,``--file`` 用本地文件。
    卸载请用 ``uninstall``,Minecraft 版本管理请用 ``list`` / ``remove``。
    """
    _begin("update", ctx)
    binary = running_binary()
    if channel not in ("release", "beta"):
        _fail(f"未知更新渠道: {channel}(可选 release、beta)")
    reporter = _reporter(ctx)
    if check:
        try:
            current = check_self_update(binary, version=version, channel=channel, reporter=reporter)
        except Exception as exc:
            _fail(str(exc), code="E_RUNTIME")
        _emit({"mode": "check", "current": current.current, "latest": current.latest, "available": current.available})
        if not _json_mode():
            _console.print(f"当前版本: [bold]{current.current}[/bold]")
            _console.print(f"最新版本: [bold]{current.latest}[/bold]")
            if current.available:
                _console.print("[yellow]有新版本可用:orzmc update[/yellow]")
            else:
                _console.print("[success]已是最新版本[/success]")
        return
    sink = _progress()
    try:
        applied = update_self(
            binary,
            version=version,
            channel=channel,
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
        _fail(str(exc), code="E_RUNTIME")
    finally:
        sink.close()
    _emit({"mode": "update", "applied": applied is not None})
    if applied is None and not _json_mode():
        _console.print("[yellow]已取消升级[/yellow]")


@app.command("list")
def list_command(ctx: typer.Context, root_dir: RootDir = None) -> None:
    """列出已安装的版本(客户端 / 服务端类型)。"""
    _begin("list", ctx)
    root_dir = root_dir or ctx.obj.get("root_dir")
    items = list_versions(root_dir=root_dir)
    _emit(
        {
            "versions": [
                {"version": item.version, "client": item.has_client, "servers": item.server_types} for item in items
            ]
        }
    )
    if _json_mode():
        return
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
    ctx: typer.Context,
    version: Annotated[str | None, typer.Option("--version", "-v", help="要备份的版本(缺省自动推断)")] = None,
    game_type: Annotated[str, typer.Option("--type", "-t", help="服务端类型")] = "vanilla",
    root_dir: RootDir = None,
) -> None:
    """备份服务端世界到 ``backup/worlds/``。"""
    _begin("backup", ctx)
    root_dir = root_dir or ctx.obj.get("root_dir")
    resolved = _infer_backup_version(version, root_dir)
    try:
        dest = backup_world(resolved, game_type=game_type, root_dir=root_dir)
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    _emit({"version": resolved, "type": game_type, "destination": dest})
    if not _json_mode():
        _console.print(f"[success]备份完成:[/success] {dest}")


@app.command()
def plugin(
    ctx: typer.Context,
    search: Annotated[str | None, typer.Option("--search", "-s", help="按插件名称搜索 Hangar")] = None,
    list_all: Annotated[bool, typer.Option("--list", "-l", help="列出默认服务器插件信息")] = False,
    output: Annotated[str | None, typer.Option("--output", "-o", help="下载插件到该目录")] = None,
    version: Annotated[str | None, typer.Option("--version", "-v", help="指定插件适配的游戏版本")] = None,
    count: Annotated[int, typer.Option("--count", "-c", help="搜索结果最大数量, 默认 3")] = 3,
) -> None:
    """Hangar 搜索 / 下载服务端插件。"""
    _begin("plugin", ctx)
    service = PluginService(reporter=_reporter(ctx))
    if search is not None:
        results = service.search(search, count)
        _emit({"mode": "search", "count": len(results), "results": [_project_json(p) for p in results]})
        if not _json_mode():
            if not results:
                _console.print("[yellow]搜索无结果[/yellow]")
            for project in results:
                _console.print(_project_line(project))
        return
    if list_all and output is None:
        found, not_found = service.list_all()
        _emit({"mode": "list", "plugins": [p.get("name") for p in found], "notFound": not_found})
        if not _json_mode():
            for project in found:
                _console.print(_project_line(project))
            if not_found:
                _console.print(f"[yellow]未获取插件信息: {', '.join(not_found)}[/yellow]")
        return
    if output is None:
        _fail("未指定有效的插件下载目录路径 (--output)")
    sink = _progress()
    try:
        files = PluginService(reporter=_reporter(ctx), sink=sink).download(output, version)
    except Exception as exc:
        _fail(str(exc), code="E_RUNTIME")
    finally:
        sink.close()
    _emit({"mode": "download", "directory": output, "files": files})
    if not _json_mode():
        _console.print(f"[success]文件已下载到目录:[/success] {output}")


def _project_json(project: dict[str, Any]) -> dict[str, Any]:
    stats = project.get("stats") or {}
    return {
        "name": project.get("name"),
        "description": project.get("description"),
        "downloads": stats.get("downloads"),
        "stars": stats.get("stars"),
    }


def _project_line(project: dict[str, Any]) -> str:
    stats = project.get("stats") or {}
    return f"{project.get('name')} — {project.get('description') or ''} (downloads={stats.get('downloads', 0)})"


@app.command("app")
def app_command(
    ctx: typer.Context,
    action: Annotated[str, typer.Argument(help="install|open|uninstall|status")],
    channel: Annotated[str, typer.Option("--channel", help="release(默认)|appstore")] = "release",
    version: Annotated[str | None, typer.Option("--version", "-v", help="指定应用版本(tag)")] = None,
    yes: Yes = False,
) -> None:
    """跨面:安装 / 打开 / 卸载 GUI 应用(macOS)。"""
    _begin("app", ctx)
    installer = AppInstaller(reporter=_reporter(ctx))
    # 规范 §8:跨面安装的是 macOS `.app`——其它平台**一律** E_UNSUPPORTED,
    # 且在碰网络/文件之前就返回(不假装成功、不降级成 E_RUNTIME)。
    if not installer.available:
        _fail(_APP_UNSUPPORTED, code="E_UNSUPPORTED")
    if channel not in ("release", "appstore"):
        _fail(f"未知渠道: {channel}(可选 release、appstore)")
    action = action.lower()
    if action == "status":
        state = installer.status()
        _emit({"installed": state.installed, "path": state.path, "version": state.version})
        if not _json_mode():
            if state.installed:
                _console.print(f"[success]已安装:[/success] {state.path} ({state.version or '?'})")
            else:
                _console.print("未安装 OrzMC 应用")
        return
    if action == "open":
        try:
            installer.open()
        except Exception as exc:
            _fail(str(exc), code="E_RUNTIME")
        _emit({"mode": "open", "path": installer.installed_path})
        return
    if action == "uninstall":
        if not yes and is_interactive() and not Confirm.ask(f"卸载 {installer.installed_path}?", default=False):
            _fail("已取消卸载", code="E_CANCELLED")
        try:
            removed = installer.uninstall()
        except Exception as exc:
            _fail(str(exc), code="E_RUNTIME")
        _emit({"mode": "uninstall", "removed": removed})
        if not _json_mode() and removed:
            _console.print("[success]已卸载 OrzMC 应用[/success]")
        return
    if action == "install":
        if channel == "appstore":
            _fail("App Store 渠道尚未上架;请用 --channel release", code="E_UNSUPPORTED")
        if not yes and is_interactive():
            prompt = f"安装 OrzMC 到 {installer.applications_dir()}?"
            if not Confirm.ask(prompt, default=True):
                _fail("已取消安装", code="E_CANCELLED")
        try:
            path = installer.install(version)
        except Exception as exc:
            _fail(str(exc), code="E_RUNTIME")
        _emit({"mode": "install", "path": path})
        if not _json_mode():
            _console.print(f"[success]已安装:[/success] {path}")
        return
    _fail(f"未知操作: {action}(可选 install|open|uninstall|status)")


@app.command()
def version(ctx: typer.Context) -> None:
    """打印工具版本。"""
    _begin("version", ctx)
    _emit({"tool": APP_VERSION, "library": LIB_VERSION, "disclaimer": disclaimer_text()})
    if not _json_mode():
        _console.print(f"orzmc {APP_VERSION} (库 {LIB_VERSION})")
        _console.print()
        _console.print(disclaimer_text(), style="dim", soft_wrap=True)


def _prog_name(ctx: typer.Context) -> str:
    return ctx.find_root().info_name or "orzmc"


def _complete_var(ctx: typer.Context) -> str:
    return "_{}_COMPLETE".format(_prog_name(ctx).replace("-", "_").upper())


def _capabilities_data() -> dict[str, object]:
    """机器可读的命令 / 选项 / 退出码清单(规范 §8;内省见 ``introspect``)。"""
    return {
        "tool": "orzmc",
        "version": APP_VERSION,
        "libraryVersion": LIB_VERSION,
        "exitCodes": [
            {"code": 0, "meaning": "成功"},
            {"code": 1, "meaning": "运行错误(错误信息写 stderr)"},
            {"code": 130, "meaning": "用户取消(Ctrl-C / 拒绝确认)"},
        ],
        "envelope": {"ok": "bool", "command": "string", "data": "object|null", "error": "{code,message}|null"},
        "commands": command_specs(app),
    }


@app.command()
def capabilities(ctx: typer.Context) -> None:
    """打印机器可读的能力清单(命令 / 选项 / 退出码;规范 §8)。"""
    _begin("capabilities", ctx)
    data = _capabilities_data()
    _emit(data)
    if not _json_mode():
        cmds = data["commands"]
        assert isinstance(cmds, list)
        for command in cmds:
            _console.print(f"[bold]{command['name']}[/bold] — {command['help']}")
            for opt in command["options"]:
                names = ", ".join(opt["names"]) or str(opt["type"])
                _console.print(f"    {names}  {opt['help'] or ''}")


@app.command()
def mcp(ctx: typer.Context) -> None:
    """本地 MCP server(stdio JSON-RPC 2.0;规范 §8.2)。"""
    _begin("mcp", ctx)
    # 延迟 import:``mcp`` 模块复用本模块的 Typer 应用,顶层 import 会成环。
    from orzmc_app.cli.mcp import serve

    serve(app)


@app.command()
def completion(
    ctx: typer.Context,
    target: Annotated[
        str,
        typer.Argument(help="bash|zsh|fish|powershell 打印补全脚本;install 写入 shell 补全目录"),
    ],
) -> None:
    """生成 / 安装 Shell 补全(规范 §9)。"""
    _begin("completion", ctx)
    if target == "install":
        # shell 由 ``$SHELL`` 决定,不做进程探测(见 ``completions`` 模块说明)。
        shell = resolve_shell()
        if shell is None:
            _fail(
                "无法判断当前 shell($SHELL 未设置);请用 `orzmc completion <shell>` 生成脚本并按提示安装",
                code="E_USAGE",
            )
        script = get_completion_script(prog_name=_prog_name(ctx), complete_var=_complete_var(ctx), shell=shell)
        try:
            result = install_completion(shell, script)
        except UnsupportedInstall as exc:
            # 规范 F6:powershell/pwsh 只生成脚本、不落盘 —— 明确报「不支持自动安装」,
            # 不要用 ok:true + path:null 让脚本误以为装好了。
            _fail(str(exc), code="E_UNSUPPORTED")
        # 登记进 install.conf,`uninstall` 才清得掉(install-update.md §9 / F2)。
        # 尽力而为:没有安装记录(venv / 开发环境)或写盘失败都不该让补全安装失败。
        try:
            record_completion_file(str(result.path))
        except Exception as exc:  # pragma: no cover - IO 异常,不影响补全本身
            _reporter(ctx).debug(f"未能登记补全路径: {exc}")
        _emit({"mode": "install", "shell": result.shell, "path": str(result.path), "hint": result.hint or None})
        if not _json_mode():
            _console.print(f"[success]已安装 {shell} 补全:[/success] {result.path}")
            if result.hint:
                _console.print(result.hint, style="dim")
        return
    if target not in _COMPLETION_SHELLS:
        _fail(f"不支持的 shell: {target}(可选: {', '.join(sorted(_COMPLETION_SHELLS))}, install)")
    script = get_completion_script(prog_name=_prog_name(ctx), complete_var=_complete_var(ctx), shell=target)
    if _json_mode():
        _emit({"mode": "script", "shell": target, "script": script})
    else:
        sys.stdout.write(script + "\n")


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
