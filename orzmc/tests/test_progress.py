"""RichProgress rendering: live start (regression), indeterminate vs known totals.

``console=Console(file=StringIO, force_terminal=True)`` makes rich render
without a real TTY, so the desktop UI path is covered by tests.
"""

from __future__ import annotations

import io
import time as _time
from types import SimpleNamespace

from rich.console import Console

from orzmc.infra.progress import NullProgress, ProgressSink, RichProgress, _count_column, _percentage_column


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


class TestTaskKinds:
    """字节任务显示大小/速度/剩余时间;计数任务显示原始计数与百分比。

    同一个 sink 要服务两类任务 —— 速度列不能给两者都用:rich 把它格式化成
    「每秒多少字节」,用在文件计数上就是胡说。
    """

    def test_byte_task_renders_size_speed_and_eta(self) -> None:
        sink, buffer = _sink()
        sink.start_bytes("下载客户端 26.2", 40_000_000)
        sink.advance(20_000_000)
        _time.sleep(0.02)  # rich 用「采样队列」估速度:需要两次带时间间隔的更新
        sink.advance(20_000_000)
        sink._progress.refresh()
        out = buffer.getvalue()
        assert "40.0/40.0 MB" in out  # DownloadColumn:人类可读大小
        assert "B/s" in out  # TransferSpeedColumn
        sink.finish()
        sink.close()

    def test_item_task_shows_counts_without_speed(self) -> None:
        sink, buffer = _sink()
        sink.start("下载资源文件(5057)", 5057)
        sink.advance(2100)
        sink._progress.refresh()
        out = buffer.getvalue()
        assert "2100" in out and "41.5%" in out
        assert "B/s" not in out
        sink.finish()
        sink.close()

    def test_columns_switch_when_a_sink_is_reused(self) -> None:
        sink, buffer = _sink()
        sink.start("批量", 10)
        sink.advance(10)
        sink.finish()
        sink.start_bytes("单个大文件", 2048)
        sink.advance(2048)
        sink.finish()
        sink.close()
        out = buffer.getvalue()
        assert "批量" in out and "单个大文件" in out
        assert "B" in out  # 字节任务用人类可读大小


def test_null_progress_accepts_byte_tasks() -> None:
    sink = NullProgress()
    sink.start_bytes("x", 1)
    sink.advance(1)
    sink.finish()


def test_default_start_bytes_delegates_to_start() -> None:
    """自定义 sink 只需实现 start/advance/finish 就能工作(向后兼容)。"""

    class _Minimal(ProgressSink):
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None]] = []

        def start(self, desc: str, total: int | None = None) -> None:
            self.calls.append((desc, total))

        def advance(self, n: int = 1) -> None: ...

        def finish(self) -> None: ...

    sink = _Minimal()
    sink.start_bytes("下载", 123)
    assert sink.calls == [("下载", 123)]


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
