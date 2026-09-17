"""RichProgress rendering: live start (regression), indeterminate vs known totals.

``console=Console(file=StringIO, force_terminal=True)`` makes rich render
without a real TTY, so the desktop UI path is covered by tests.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from orzmc.infra.progress import NullProgress, RichProgress, _count_column, _percentage_column


def _sink() -> tuple[RichProgress, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=120)
    return RichProgress(console=console), buffer


class TestRichProgress:
    def test_status_starts_the_live_display(self) -> None:
        # 回归:``Progress.live`` 是 Live 实例(恒真),旧 ``if not live`` 判断
        # 让 _ensure_live 永不调用 start → 整个进度条从未渲染(用户看不到任何反馈)。
        sink, buffer = _sink()
        assert sink.is_live is False
        sink.status("获取 Minecraft 版本清单")
        assert sink.is_live is True
        sink.finish()
        sink.close()
        assert sink.is_live is False
        assert "获取 Minecraft 版本清单" in buffer.getvalue()

    def test_byte_progress_renders_the_task_line(self) -> None:
        sink, buffer = _sink()
        sink.start("下载版本元数据 26.3", 1000)
        sink.advance(750)
        sink.finish()
        sink.close()
        assert "下载版本元数据 26.3" in buffer.getvalue()

    def test_known_total_shows_a_percentage(self) -> None:
        sink, buffer = _sink()
        sink.start("批量下载", 200)
        sink.advance(50)
        sink._progress.refresh()  # 强刷一帧:rich 的 Live 是后台 ~10Hz,测试里瞬时完成抓不到中间帧
        assert "25.0%" in buffer.getvalue()
        sink.finish()
        sink.close()

    def test_indeterminate_task_hides_the_percentage(self) -> None:
        # 无总量时 percentage 恒为 0.0% —— 旁边还有呼吸条,显示 0.0% 会像卡死。
        sink, buffer = _sink()
        sink.status("获取版本清单")
        sink.advance(5)
        sink.finish()
        sink.close()
        assert "0.0%" not in buffer.getvalue()

    def test_side_by_side_tasks_reuse_one_line(self) -> None:
        # one shared sink: start() reconfigures instead of stacking lines
        sink, buffer = _sink()
        sink.start("第一步", 10)
        sink.advance(10)
        sink.finish()
        sink.start("第二步", 10)
        sink.advance(10)
        sink.finish()
        sink.close()
        out = buffer.getvalue()
        assert "第一步" in out and "第二步" in out

    def test_null_progress_is_a_noop(self) -> None:
        NullProgress().status("任意")
        NullProgress().start("x", 1)
        NullProgress().advance(1)
        NullProgress().finish()


class TestCountColumn:
    def test_indeterminate_task_hides_the_count(self) -> None:
        assert str(_count_column().render(SimpleNamespace(total=None, completed=3))) == ""

    def test_known_total_shows_the_completed_count(self) -> None:
        assert str(_count_column().render(SimpleNamespace(total=1000, completed=250))) == "250"


class TestPercentageColumn:
    def test_indeterminate_task_hides_the_percentage(self) -> None:
        assert str(_percentage_column().render(SimpleNamespace(total=None, completed=3, percentage=0.0))) == ""

    def test_known_total_shows_the_percentage(self) -> None:
        assert str(_percentage_column().render(SimpleNamespace(total=100, completed=25, percentage=25.0))) == "25.0%"
