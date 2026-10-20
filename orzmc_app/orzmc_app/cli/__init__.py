"""Typer CLI entry point for orzmc-app.

The console script ``orzmc`` maps to :func:`main`.
"""

from __future__ import annotations

import sys

import typer

from orzmc_app.cli.app import app


def _reconfigure_utf8(stream: object) -> None:
    """把单个标准流重配置为 UTF-8;不可重配置 / 出错则静默跳过。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def _force_utf8_stdio() -> None:
    """Windows 下 stdout/stderr 被重定向时按 ANSI 代码页(如 cp1252)编码,
    打印中文会 UnicodeEncodeError(crash)。强制 UTF-8,管道 / 重定向输出不崩。
    交互终端不受影响(console 本就按 UTF-8 处理,见 PEP 528)。"""
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)


def _forwarded_argv(argv: list[str]) -> list[str] | None:
    """`ORZMC_RUNTIME=swift` 时该转发给真身的 argv;不该转发则返回 ``None``。

    规则很简单:

    - 判定结果是 ``python`` -> 不转发(规范 §10.1「默认判定」:存量安装保持 Python);

    - `runtime` 的管理命令 -> 留在 Python 侧(台账里 python lead / swift na);

    - **其余一律转发**,包括 `--version` / `--help` / 裸 `orzmc`:
      规范 `install-update.md` §10.1 要求版本号报**真身**版本,帮助也应是真身的命令面。
    """
    from orzmc.services.runtime import resolve_runtime

    if resolve_runtime()[0] != "swift":
        return None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("--root-dir", "--min-mem", "--max-mem"):
            index += 2  # 跳过它的值(值可能恰好叫 runtime)
            continue
        if item == "runtime":
            return None
        if not item.startswith("-"):
            break
        index += 1
    return argv


def _forward(argv: list[str]) -> int:
    """把 argv 原样交给 Swift 真身;通道错误按规范错误码呈现。"""
    import json

    from orzmc.services.runtime import RuntimeChannelError, forward_or_report

    try:
        return forward_or_report(argv)
    except RuntimeChannelError as exc:
        if "--json" in argv:
            positional = [item for item in argv if not item.startswith("-")]
            payload = {
                "ok": False,
                # 真身没跑起来,command 只能按 argv 推断(第一个位置参数)。
                "command": positional[0] if positional else "",
                "data": None,
                "error": {"code": exc.code, "message": str(exc)},
            }
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    _force_utf8_stdio()
    forwarded = _forwarded_argv(sys.argv[1:])
    if forwarded is not None:
        return _forward(forwarded)
    try:
        app()
        return 0
    except SystemExit as exc:
        # click 在 standalone 模式下用 ``sys.exit`` 收尾:``--help`` 是 0,用法错误是
        # **2**。规范 §6 的退出码只有 0 / 1 / 130,故把非 0 的收敛成 1(130 = 取消)。
        code = exc.code if isinstance(exc.code, int) else 0
        return 0 if code == 0 else (130 if code == 130 else 1)
    except typer.Exit as exc:  # pragma: no cover - click 已把它转成 SystemExit
        return exc.exit_code or 0
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
    except Exception:  # pragma: no cover - unexpected bug, keep a traceback
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
