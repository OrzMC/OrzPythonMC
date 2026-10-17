"""RichReporter:外部注入的 Console 也必须能渲染语义样式(回归)。

``orzmc --json <cmd>`` 会把日志切到 stderr,做法是给 ``RichReporter`` 传一个
``Console(stderr=True)``。那个 Console 不带 ``ORZMC_THEME``,于是第一次
``info()`` / ``warn()`` 就 ``rich.errors.MissingStyle`` —— 被 CLI 兜成
``E_RUNTIME``,`--json` 下任何有日志的命令都会失败(真机踩过)。
"""

from __future__ import annotations

import io

from rich.console import Console

from orzmc.infra.log import RichReporter


def _reporter() -> tuple[RichReporter, io.StringIO]:
    buffer = io.StringIO()
    # 故意不传 theme:模拟 CLI 层自己 new 的 Console(stderr=True)。
    return RichReporter(console=Console(file=buffer, width=200)), buffer


def test_semantic_styles_work_on_injected_console() -> None:
    reporter, buffer = _reporter()
    for method in (reporter.info, reporter.warn, reporter.error, reporter.success, reporter.plain):
        method("hello")
    assert buffer.getvalue().count("hello") == 5


def test_debug_only_in_verbose() -> None:
    reporter, buffer = _reporter()
    reporter.debug("hidden")
    assert "hidden" not in buffer.getvalue()
    quiet, quiet_buffer = _reporter()
    quiet.verbose = True
    quiet.debug("shown")
    assert "shown" in quiet_buffer.getvalue()
