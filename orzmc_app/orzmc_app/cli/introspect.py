"""Typer 应用内省:生成 ``capabilities`` 数据(规范 §8.1)。

``capabilities`` 命令与 ``mcp`` server 共用同一份内省结果,避免“手写两遍”漂移。
"""

from __future__ import annotations

from typing import Any, cast

import typer
import typer.main


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
        options: list[dict[str, object]] = []
        for param in command.params:
            names = [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]
            if not names:
                continue
            options.append(
                {
                    "names": [str(item) for item in names],
                    "type": getattr(param.type, "name", None),
                    "required": bool(getattr(param, "required", False)),
                    "flag": bool(getattr(param, "is_flag", False)),
                    "default": jsonable(getattr(param, "default", None)),
                    "help": getattr(param, "help", None),
                }
            )
        doc = (command.callback.__doc__ or "").strip().splitlines()
        specs.append({"name": name, "help": doc[0] if doc else "", "options": options})
    return specs
