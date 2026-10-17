"""Typer 应用内省:生成 ``capabilities`` 数据(规范 §8.1)。

``capabilities`` 命令与 ``mcp`` server 共用同一份内省结果,避免“手写两遍”漂移。

枚举值:规范 `docs/spec/cli-surface.json` 的 `enum` 是**目标**,``ENUM_VALUES`` 是**实现现状**。
``capabilities`` 会把它随选项一起上报,父仓库 ``scripts/verify/surface.py`` 据此比对——
声明了却没实现(如 ``app --channel appstore``)会变成已声明差异/红灯,而不是静静漂移。
"""

from __future__ import annotations

from typing import Any, cast

import typer
import typer.main

# 各命令实际支持的枚举值,键为选项长名/位置参数名(详见模块 docstring)。
ENUM_VALUES: dict[str, dict[str, list[str]]] = {
    "client": {"type": ["vanilla", "fabric", "forge"]},
    "server": {"type": ["vanilla", "paper", "fabric", "forge"]},
    "remove": {"type": ["vanilla", "paper", "fabric", "forge"]},
    "backup": {"type": ["vanilla", "paper", "fabric", "forge"]},
    "update": {"channel": ["release", "beta"]},
    # App Store 渠道尚未上架(services/app.py 显式不支持),实际只支持 release。
    "app": {"action": ["install", "open", "uninstall", "status"], "channel": ["release"]},
    "completion": {"target": ["bash", "zsh", "fish", "powershell", "pwsh", "install"]},
}


def _enum_key(names: list[str]) -> str | None:
    """选项/位置参数的规范键: ``--x`` -> ``x``; ``-x`` -> ``x``; 位置参数原名。"""
    for name in names:
        if name.startswith("--"):
            return name[2:]
    for name in names:
        if name.startswith("-"):
            return name[1:]
    return names[0] if names else None


def jsonable(value: object) -> object:
    """把选项默认值转成可 JSON 序列化的标量(其余一律 ``str``)。"""
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def command_specs(tool_app: typer.Typer) -> list[dict[str, object]]:
    """内省 Typer 应用:每条命令的 ``name`` / ``help`` / ``options``。

    ``options[].names`` 对选项是 ``--long`` 与 ``-s`` 等(顺序同声明);位置参数
    (``TyperArgument``)的 ``opts`` 就是它的名字(如 ``["action"]``),同样列入。
    """
    group = cast(Any, typer.main.get_command(tool_app))
    specs: list[dict[str, object]] = []
    for name in sorted(group.commands):
        command = group.commands[name]
        declared_enums = ENUM_VALUES.get(name, {})
        options: list[dict[str, object]] = []
        for param in command.params:
            names = [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]
            if not names:
                continue
            key = _enum_key([str(item) for item in names])
            enum_values = declared_enums.get(key) if key else None
            options.append(
                {
                    "names": [str(item) for item in names],
                    "type": getattr(param.type, "name", None),
                    "required": bool(getattr(param, "required", False)),
                    "flag": bool(getattr(param, "is_flag", False)),
                    "default": jsonable(getattr(param, "default", None)),
                    "help": getattr(param, "help", None),
                    "enum": list(enum_values) if enum_values else None,
                }
            )
        doc = (command.callback.__doc__ or "").strip().splitlines()
        specs.append({"name": name, "help": doc[0] if doc else "", "options": options})
    return specs
