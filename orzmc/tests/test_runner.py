"""ProcessRunner tests: streamed output + graceful Ctrl-C reaping.

The interrupt tests drive real subprocesses (no java, no network). The full
``run_stream`` KeyboardInterrupt path needs a terminal-delivered SIGINT to the
foreground group — which would also interrupt pytest — so the reaping logic is
tested through :func:`_shutdown_after_interrupt` directly.
"""

from __future__ import annotations

import locale
import subprocess
import sys

from fakes import FakeReporter

import orzmc.infra.runner as runner_mod
from orzmc.infra.runner import ProcessRunner, _shutdown_after_interrupt


class TestProcessRunner:
    def test_run_stream_forwards_lines_and_exit_code(self, reporter: FakeReporter) -> None:
        args = [sys.executable, "-c", "print('line a'); print('line b'); raise SystemExit(3)"]
        lines: list[str] = []
        code = ProcessRunner(reporter).run_stream(args, on_line=lines.append)
        assert lines == ["line a", "line b"]
        assert code == 3
        assert reporter.lines[0] == ("debug", "$ " + " ".join(args))

    def test_run_stream_forwards_output_without_on_line(self, reporter: FakeReporter) -> None:
        args = [sys.executable, "-c", "print('no handler')"]
        assert ProcessRunner(reporter).run_stream(args) == 0

    def test_run_stream_tolerates_undecodable_bytes(self, reporter: FakeReporter, monkeypatch) -> None:
        # JVM children (Fabric/Forge installers) write the system ANSI codepage
        # (e.g. GBK on zh-CN); under PYTHONUTF8 the parent decodes as UTF-8, so
        # a lone 0xd5 must degrade to a replacement char, not raise
        # UnicodeDecodeError and kill the deploy. Force the parent's decode to
        # utf-8 so the test is deterministic on every locale.
        monkeypatch.setattr(locale, "getencoding", lambda: "utf-8")
        monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=False: "utf-8")
        args = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(bytes([0xd5, 0x0a])); sys.stdout.buffer.flush()",
        ]
        lines: list[str] = []
        code = ProcessRunner(reporter).run_stream(args, on_line=lines.append)
        assert code == 0
        assert lines == ["�"]

    def test_shutdown_after_interrupt_waits_for_graceful_exit(self, monkeypatch) -> None:
        monkeypatch.setattr(runner_mod, "_INTERRUPT_WAIT", 10.0)
        # The child prints during its "save" then exits on its own; the drain
        # thread must forward those lines via on_line while we wait.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; print('saving...', flush=True); time.sleep(0.2)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines: list[str] = []
        _shutdown_after_interrupt(proc, FakeReporter(), on_line=lines.append)
        assert lines == ["saving..."]
        assert proc.poll() == 0

    def test_shutdown_after_interrupt_escalates_to_terminate_kill(self, monkeypatch) -> None:
        monkeypatch.setattr(runner_mod, "_INTERRUPT_WAIT", 0.5)
        monkeypatch.setattr(runner_mod, "_INTERRUPT_KILL", 0.5)
        # Child ignores SIGINT and SIGTERM and blocks on stdin forever, so the
        # grace period expires and the helper escalates. (On Windows
        # terminate() is TerminateProcess and kills immediately; either way the
        # child is gone.)
        child = (
            "import signal, sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "sys.stdin.read()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _shutdown_after_interrupt(proc, FakeReporter())
        assert proc.poll() is not None


def test_normalize_exit_code() -> None:
    """POSIX 信号终止(返回 -N)归一为 128+N;非负原样(对齐 B7 / Swift)."""
    from orzmc.infra.runner import normalize_exit_code

    assert normalize_exit_code(0) == 0
    assert normalize_exit_code(3) == 3
    assert normalize_exit_code(-15) == 143
    assert normalize_exit_code(-9) == 137
