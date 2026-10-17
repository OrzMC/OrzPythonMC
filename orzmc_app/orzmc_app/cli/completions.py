"""Shell 补全脚本的落点选择与安装(规范 `cli-surface.md` §9、`frontends.md` F6)。

**为什么不用 typer 自带的 `install()`**:

1. 它要靠 ``shellingham`` 探测当前 shell;而 shellingham 在 ``__init__.py`` 里用
   ``importlib.import_module(f".{os.name}")`` **动态**导入平台实现,PyInstaller
   的静态分析看不到 → 发布的单文件二进制里 ``shellingham.posix`` / ``shellingham.nt``
   缺失 → 任何探测路径都直接 ``RuntimeError: Shell detection not implemented for 'posix'``
   (2.11.0 及更早的真实发布版踩过,``--show-completion`` / ``completion install`` 全崩)。
2. 它会**重写** ``~/.bashrc`` / ``~/.zshrc``,Windows 上还会执行
   ``Set-ExecutionPolicy Unrestricted -Scope CurrentUser`` —— 一个补全安装没有理由
   改用户的执行策略。

这里只用标准库:shell 由 ``-`` / 环境变量显式决定,**幂等覆盖**固定落点,
唯一会写的 rc 是 zsh 的 ``~/.zshrc`` 里那一行 fpath(追加一次、带标记、已存在就不再写)。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

#: 程序名:落点文件名与补全函数名都基于它。
PROG_NAME = "orzmc"

#: 支持的 shell(与 ``capabilities`` 上报的 ``completion.target`` 枚举保持一致)。
SHELLS: tuple[str, ...] = ("bash", "zsh", "fish", "powershell", "pwsh")

#: 按 ``frontends.md`` F6:``powershell`` / ``pwsh`` 只生成脚本、不落盘安装。
SCRIPT_ONLY_SHELLS = frozenset({"powershell", "pwsh"})

_ZSH_COMPLETION_DIR = ".zsh/completions"
_ZSH_FPATH_SNIPPET = "fpath+=(~/.zsh/completions)"
_ZSH_RC_BLOCK = (
    "# added by orzmc completion install(zsh 需要 fpath 含 ~/.zsh/completions)\n"
    f"{_ZSH_FPATH_SNIPPET}; autoload -Uz compinit; compinit\n"
)


class InstallResult(NamedTuple):
    """安装结果:落点(``None`` = 该 shell 不落盘)、给用户看的提示。"""

    shell: str
    path: Path | None
    hint: str


def normalize_shell(name: str | None) -> str | None:
    """``/bin/zsh`` / ``ZSH.EXE`` / ``pwsh`` → 规范 shell 名;不认识返回 ``None``。"""
    if not name:
        return None
    base = os.path.basename(name.replace("\\", "/").strip()).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if base in SHELLS else None


def resolve_shell(env: Mapping[str, str] | None = None) -> str | None:
    """从环境变量推断当前 shell;推断不出返回 ``None``(调用方报错让用户显式指定)。

    只读环境变量、**不做进程探测**(见模块 docstring 第 1 条)。
    """
    environ = os.environ if env is None else env
    shell = normalize_shell(environ.get("SHELL"))
    if shell is not None:
        return shell
    # Windows:PowerShell 是所有受支持 Windows 版本的默认 shell(5.1 起内置)。
    if environ.get("PSModulePath") or os.name == "nt":
        return "powershell"
    return None


def target_path(shell: str, home: Path) -> Path | None:
    """补全脚本落点(规范 ``frontends.md`` 数据归属表);``powershell`` 不落盘。"""
    if shell == "bash":
        return home / ".local" / "share" / "bash-completion" / "completions" / PROG_NAME
    if shell == "zsh":
        return home / _ZSH_COMPLETION_DIR / f"_{PROG_NAME}"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / f"{PROG_NAME}.fish"
    return None


def install(shell: str, script: str, *, home: Path | None = None) -> InstallResult:
    """幂等覆盖补全脚本(同路径覆盖即为最新),返回落点与提示。"""
    if shell not in SHELLS:
        raise ValueError(f"不支持的 shell: {shell}")
    if shell in SCRIPT_ONLY_SHELLS:
        return InstallResult(
            shell,
            None,
            f"{shell} 不自动落盘(规范 F6):把 `orzmc completion {shell}` 的输出加入 $PROFILE",
        )
    base = Path.home() if home is None else home
    path = target_path(shell, base)
    assert path is not None  # SCRIPT_ONLY_SHELLS 已提前返回
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script.rstrip("\n") + "\n", encoding="utf-8")
    if shell == "zsh":
        hint = _ensure_zsh_fpath(base)
    elif shell == "bash":
        hint = "需要 bash-completion(多数发行版默认安装);若未生效,在 ~/.bashrc 里 source 上面这个文件"
    else:
        hint = ""
    return InstallResult(shell, path, hint)


def _ensure_zsh_fpath(home: Path) -> str:
    """确保 ``~/.zshrc`` 里有一次 ``fpath+=(~/.zsh/completions)``;已存在则不动。"""
    rc = home / ".zshrc"
    existing = rc.read_text(encoding="utf-8", errors="replace") if rc.is_file() else ""
    if _ZSH_FPATH_SNIPPET in existing:
        return ""
    with rc.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(_ZSH_RC_BLOCK)
    return f"已在 {rc} 追加 fpath 配置(带 orzmc 标记);重启 shell 生效"
