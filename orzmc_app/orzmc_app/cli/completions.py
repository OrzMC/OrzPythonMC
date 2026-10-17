"""Shell 补全脚本的落点选择与安装(规范 `cli-surface.md` §9、`frontends.md` F6)。

**为什么不用 typer 自带的 `install()`**:

1. 它要靠 ``shellingham`` 探测当前 shell;而 shellingham 在 ``__init__.py`` 里用
   ``importlib.import_module(f".{os.name}")`` **动态**导入平台实现,PyInstaller
   的静态分析看不到 → 发布的单文件二进制里 ``shellingham.posix`` / ``shellington.nt``
   缺失 → 任何探测路径都直接 ``RuntimeError: Shell detection not implemented for 'posix'``
   (2.11.0 及更早的真实发布版踩过,``--show-completion`` / ``completion install`` 全崩)。
2. 它会**重写** ``~/.bashrc`` / ``~/.zshrc``,Windows 上还会执行
   ``Set-ExecutionPolicy Unrestricted -Scope CurrentUser`` —— 一个补全安装没有理由
   改用户的执行策略。

这里只用标准库:shell 由 ``$SHELL`` / ``PSModulePath`` 决定(规范禁止进程探测),
落点是 `frontends.md` 数据归属表里的固定路径,**幂等覆盖**;为了让脚本真的被加载,
只在必要时补一行带标记的 rc(已存在则不再写)。
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
#: 因此 ``completion install`` 对它们返回 ``E_UNSUPPORTED``(而不是假装成功)。
SCRIPT_ONLY_SHELLS = frozenset({"powershell", "pwsh"})

_ZSH_COMPLETION_DIR = ".zsh/completions"
# 检测用片段(用户可能自己写过别的写法,只要含这一句就不再追加)
_ZSH_FPATH_SNIPPET = "fpath+=(~/.zsh/completions)"
# 实际写入的整行:光加 fpath 不够,还要 compinit 才会生效
_ZSH_RC_LINE = f"{_ZSH_FPATH_SNIPPET}; autoload -Uz compinit; compinit"
_ZSH_RC_MARKER = "# added by orzmc completion install(zsh 需要 fpath 含 ~/.zsh/completions)"


class UnsupportedInstall(ValueError):
    """该 shell 不支持自动落盘(``frontends.md`` F6):只生成脚本。"""


class InstallResult(NamedTuple):
    """安装结果:落点与给用户看的提示。"""

    shell: str
    path: Path
    hint: str


def normalize_shell(name: str | None) -> str | None:
    """``/bin/zsh`` / ``ZSH.EXE`` / ``pwsh`` → 规范 shell 名;不认识返回 ``None``。"""
    if not name:
        return None
    base = os.path.basename(name.replace("\\", "/").strip()).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if base in SHELLS else None


def resolve_shell(env: Mapping[str, str] | None = None, *, is_windows: bool | None = None) -> str | None:
    """从环境变量推断当前 shell;推断不出返回 ``None``(调用方报错让用户显式指定)。

    只读环境变量、**不做进程探测**(见模块 docstring 第 1 条)。
    Windows 上 ``$SHELL`` 通常不存在,按平台兜底成 ``powershell`` —— **不能**用
    ``PSModulePath`` 判断平台:装了 pwsh 的 Linux/macOS 也有这个变量(CI runner 就有),
    会把 Linux 误判成 Windows。
    """
    environ = os.environ if env is None else env
    shell = normalize_shell(environ.get("SHELL"))
    if shell is not None:
        return shell
    windows = os.name == "nt" if is_windows is None else is_windows
    return "powershell" if windows else None


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
    """幂等覆盖补全脚本(同路径覆盖即为最新),返回落点与提示。

    Raises:
        UnsupportedInstall: ``powershell`` / ``pwsh``(规范 F6:只生成脚本)。
        ValueError: 不认识的 shell。
    """
    if shell not in SHELLS:
        raise ValueError(f"不支持的 shell: {shell}")
    if shell in SCRIPT_ONLY_SHELLS:
        raise UnsupportedInstall(
            f"{shell} 不支持自动安装补全(规范 F6):把 `orzmc completion {shell}` 的输出加入 $PROFILE"
        )
    base = Path.home() if home is None else home
    path = target_path(shell, base)
    assert path is not None  # SCRIPT_ONLY_SHELLS 已提前抛错
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script.rstrip("\n") + "\n", encoding="utf-8")
    if shell == "bash":
        # 落点在 bash-completion 的标准目录:装了 bash-completion 会自动加载;
        # 没装(如 macOS 自带 bash 3.2)则必须 source,否则补全根本不生效。
        hint = _ensure_rc_line(
            base / ".bashrc",
            f'[ -f "{path}" ] && . "{path}"',
            marker="# added by orzmc completion install(bash 未装 bash-completion 时也能生效)",
        )
    elif shell == "zsh":
        hint = _ensure_rc_line(
            base / ".zshrc",
            _ZSH_RC_LINE,
            marker=_ZSH_RC_MARKER,
            detect=_ZSH_FPATH_SNIPPET,
        )
    else:
        hint = ""
    return InstallResult(shell, path, hint)


def _ensure_rc_line(rc: Path, line: str, *, marker: str, detect: str | None = None) -> str:
    """幂等地把 ``line`` 加进 rc 文件;``detect``(缺省=``line``)已存在则什么都不做。"""
    existing = rc.read_text(encoding="utf-8", errors="replace") if rc.is_file() else ""
    if (detect or line) in existing:
        return ""
    with rc.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"{marker}\n{line}\n")
    return f"已在 {rc} 追加一行(带 orzmc 标记);新开终端或 source 后生效"
