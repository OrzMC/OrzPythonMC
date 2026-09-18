"""ProgressSink protocol + rich default implementation.

A single shared sink is used for a whole operation: ``start(desc, total)``
reconfigures the current task, ``advance(n)`` moves it forward and
``finish()`` closes it. Callers never touch the rendering details.

Two kinds of tasks are distinguished, because the same numbers mean different
things: :meth:`ProgressSink.start` counts **items** (the ~5000 asset files)
while :meth:`ProgressSink.start_bytes` counts **bytes** (a single download).
Only the byte flavour can render a transfer speed / human-readable size / ETA.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


class ProgressSink(ABC):
    """Abstract progress display. The app layer injects its own."""

    @abstractmethod
    def start(self, desc: str, total: int | None = None) -> None:
        """Start (or reconfigure) an **item-count** task: ``advance(n)`` adds N items."""

    def start_bytes(self, desc: str, total: int | None = None) -> None:
        """Start (or reconfigure) a **byte-count** task.

        Defaults to :meth:`start`, so third-party sinks keep working; sinks that
        can render more (transfer speed, ``23.4/39.2 MB``, ETA) override it.
        """
        self.start(desc, total)

    @abstractmethod
    def advance(self, n: int = 1) -> None: ...

    @abstractmethod
    def finish(self) -> None: ...

    def status(self, desc: str) -> None:
        """Show indeterminate progress for work with no byte/total count.

        Used around metadata requests (manifest, fabric-meta, Paper, Forge) so
        a slow network is never a silent wait. Defaults to a total-less
        ``start``; the caller ends it with ``finish()``. Sinks that can only
        render known totals may override this as a no-op.
        """
        self.start(desc, None)


class NullProgress(ProgressSink):
    """No-op implementation used in tests and when progress is unwanted."""

    def start(self, desc: str, total: int | None = None) -> None: ...

    def advance(self, n: int = 1) -> None: ...

    def finish(self) -> None: ...


def _count_column() -> Any:
    """Rich column showing the completed count, blank while the total is unknown.

    Indeterminate tasks (metadata status lines, downloads without
    ``Content-Length``) have ``total is None`` — printing a hard ``0`` there
    reads as "stuck", while the pulsing bar + spinner already say "working".
    Defined lazily so importing this module never pulls rich.
    """
    from rich.progress import ProgressColumn
    from rich.text import Text

    class CountColumn(ProgressColumn):
        def render(self, task: Any) -> Any:
            return Text("") if task.total is None else Text(str(int(task.completed)))

    return CountColumn()


def _percentage_column() -> Any:
    """Percentage of the current task, blank while the total is unknown.

    ``task.percentage`` is ``0.0`` when there is no total, which would sit at
    "0.0%" next to a pulsing bar and read as stuck — so hide it, same rule as
    the count column. For file counts the percentage says more than the raw
    number ("1234 / 5057" vs "24.4%"), for byte downloads it is the familiar
    percentage bar.
    """
    from rich.progress import ProgressColumn
    from rich.text import Text

    class PercentageColumn(ProgressColumn):
        def render(self, task: Any) -> Any:
            return Text("") if task.total is None else Text(f"{task.percentage:3.1f}%")

    return PercentageColumn()


class RichProgress(ProgressSink):
    """Renders one rich progress task; reused across a whole operation.

    The column set is swapped per task kind: item tasks show raw counts, byte
    tasks show ``23.4/39.2 MB`` + ``4.1 MB/s`` + ETA. A speed column cannot be
    shown for both — rich formats it as a size per second, which would print
    nonsense for a file count.

    ``console`` (optional) is the test seam: passing a ``Console`` with
    ``force_terminal=True`` makes rendering observable without a real TTY.
    """

    def __init__(self, console: Console | None = None) -> None:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        self._item_columns = (
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            _count_column(),
            _percentage_column(),
            TimeElapsedColumn(),
            SpinnerColumn(),
        )
        self._byte_columns = (
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            SpinnerColumn(),
        )
        self._progress = Progress(*self._item_columns, console=console)
        self._task_id: Any = None

    @property
    def is_live(self) -> bool:
        # ``Progress.live`` is a Live *instance* (always truthy); only
        # ``Live.is_started`` tells whether the display is actually running.
        return self._progress.live.is_started

    def _ensure_live(self) -> None:
        if not self._progress.live.is_started:
            self._progress.start()

    def start(self, desc: str, total: int | None = None) -> None:
        self._begin(desc, total, byte=False)

    def start_bytes(self, desc: str, total: int | None = None) -> None:
        self._begin(desc, total, byte=True)

    def _begin(self, desc: str, total: int | None, *, byte: bool) -> None:
        # Swap the column set *before* the task is (re)configured, so the first
        # rendered frame already has the right shape for this task kind.
        self._progress.columns = self._byte_columns if byte else self._item_columns
        self._ensure_live()
        if self._task_id is None:
            self._task_id = self._progress.add_task(desc, total=total)
        else:
            self._progress.update(self._task_id, description=desc, total=total, completed=0)

    def advance(self, n: int = 1) -> None:
        if self._task_id is not None:
            self._progress.advance(self._task_id, n)

    def finish(self) -> None:
        if self._task_id is None:
            return
        self._progress.stop_task(self._task_id)
        self._progress.remove_task(self._task_id)
        self._task_id = None
        # No task left → take the live region down. Otherwise it keeps
        # refreshing an empty renderable and every later plain line (server
        # console output, prompts) interleaves with stray clear-line escapes.
        if self._progress.live.is_started:
            self._progress.stop()

    def close(self) -> None:
        self.finish()
