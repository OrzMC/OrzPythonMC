"""Interactive TTY prompts for the CLI (only used when stdin is a TTY)."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt

_console = Console(highlight=False)

# 惰性名称(PEP 562):remote_version_catalog / run_picker 由模块 __getattr__
# 在首次访问时导入,避免每个 CLI 命令都付 prompt_toolkit / requests 的导入成本。
# 注意:模块 __getattr__ 只对 `module.attr` 式访问生效;函数内裸全局名引用走
# LOAD_GLOBAL,不会触发它(直接 NameError,frozen 真机曾复现)。因此
# resolve_version 必须经 _self.xxx 访问。
_self: Any = sys.modules[__name__]


def __getattr__(name: str) -> Any:
    """Lazily resolve the two heavy names used only by the interactive picker.

    ``remote_version_catalog`` pulls ``orzmc`` → requests/urllib3, and
    ``run_picker`` pulls prompt_toolkit (~110ms of imports together). Loading
    them at module import would make every CLI command — even ``orzmc
    version`` — pay that cost. PEP 562 defers until the first TTY picker;
    ``monkeypatch.setattr`` in tests still works because it sets the attribute
    on the module directly, bypassing ``__getattr__``. Resolved names are
    cached into the module dict so later lookups hit the dict directly.
    """
    if name == "remote_version_catalog":
        from orzmc import remote_version_catalog as _rvc

        globals()["remote_version_catalog"] = _rvc
        return _rvc
    if name == "run_picker":
        from .picker import run_picker as _rp

        globals()["run_picker"] = _rp
        return _rp
    raise AttributeError(name)


def is_interactive() -> bool:
    """True when stdin+stdout are real terminals (safe for rich prompts)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def resolve_version(version: str | None, root_dir: str | None, refresh: bool = False) -> str | None:
    """Resolve the Minecraft version for a command.

    Explicit value → used as-is. TTY without value → interactive keyboard
    picker over the full Mojang catalog (scroll, filter, release/snapshot
    toggle, Escape → latest). Non-TTY without value → ``None``, letting the
    library fall back to the latest release. ``refresh`` forwards
    ``--refresh`` to the catalog (bypassing the 24h metadata cache).
    """
    if version:
        return version
    if not is_interactive():
        return None
    try:
        catalog = _self.remote_version_catalog(root_dir=root_dir, refresh=refresh)
    except Exception as exc:
        _console.print(f"[yellow]无法获取版本列表({exc}),回车使用最新[/yellow]")
        return None
    if not catalog:
        _console.print("[yellow]版本清单为空,回车使用最新[/yellow]")
        return None
    try:
        return _self.run_picker(catalog)
    except Exception as exc:  # never crash the CLI on TUI trouble
        _console.print(f"[yellow]版本选择器异常({exc}),回车使用最新[/yellow]")
        return None


def resolve_username(username: str | None) -> str:
    """Resolve the client player name: explicit flag / TTY ask / default guest.

    Explicit ``--username`` → as-is. TTY without a value → ask once (Enter
    keeps the default ``guest``). Non-TTY → ``guest`` so scripts stay silent.
    """
    if username:
        return username
    if not is_interactive():
        return "guest"
    return Prompt.ask("玩家名称(回车使用默认 guest)", default="guest").strip() or "guest"


def confirm_java(major: int, need_jdk: bool) -> bool:
    """Ask before installing a sandboxed Java runtime into the app dir."""
    kind = "JDK" if need_jdk else "JRE"
    return Confirm.ask(f"需要安装 Java {major}({kind}) 到应用目录,继续?", default=True)


def confirm_eula() -> bool:
    """Ask the user to accept the Minecraft EULA before starting a server."""
    return Confirm.ask("需要接受 Minecraft EULA 才能启动服务端,是否同意?", default=True)
