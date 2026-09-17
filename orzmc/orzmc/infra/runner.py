"""Process execution: long-lived streaming processes (java client/server).

Replaces the scattered ``os.system`` / ``os.popen`` calls from the old codebase.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable

from orzmc.infra.log import NullReporter, Reporter

# Grace periods for reaping a server that received Ctrl-C (same foreground
# process group, so it got SIGINT and is already saving & exiting on its own).
# The wait must cover a world save; the SIGTERM→SIGKILL ladder only fires when
# the child refuses to exit.
_INTERRUPT_WAIT = 60.0  # seconds to wait for a graceful exit after Ctrl-C
_INTERRUPT_KILL = 10.0  # seconds to wait after SIGTERM before SIGKILL


class ProcessRunner:
    """Run long-lived processes (java client/server) with streamed output."""

    def __init__(self, reporter: Reporter | None = None) -> None:
        self._reporter = reporter or NullReporter()

    def run_stream(
        self,
        args: list[str],
        on_line: Callable[[str], None] | None = None,
        cwd: str | None = None,
    ) -> int:
        """Run a foreground process, forwarding its stdout lines to ``on_line``.

        Blocks until the process exits and returns its exit code.
        """
        self._reporter.debug("$ " + " ".join(args))
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # JVM children (Fabric/Forge installers) write the system ANSI
            # codepage (e.g. GBK on zh-CN), but the parent's decode follows
            # PYTHONUTF8/locale — a mismatch must degrade to replacement chars,
            # never raise and kill the deploy. Same lossy philosophy as the
            # client log tail read in services/client.py.
            errors="replace",
            bufsize=1,
            cwd=cwd,
        )
        if proc.stdout is not None:
            try:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    if on_line:
                        on_line(line)
            except KeyboardInterrupt:
                # The terminal delivered Ctrl-C to the whole foreground group,
                # so the child got SIGINT too and is saving & exiting on its
                # own. Wait for it so the CLI doesn't orphan the server
                # mid-shutdown, then re-raise so the caller reports the cancel.
                _shutdown_after_interrupt(proc, self._reporter, on_line)
                raise
        return proc.wait()

    def run_detached(
        self,
        args: list[str],
        cwd: str | None = None,
        log_path: str | None = None,
        *,
        windows_no_window: bool = False,
    ) -> subprocess.Popen:
        """Launch a process in the background and return its handle.

        ``stdout``/``stderr`` are redirected to ``log_path`` (appended) when
        given, otherwise discarded. The caller keeps the handle so it can poll
        for early exit instead of trusting that a spawned process survives.

        ``windows_no_window`` picks ``CREATE_NO_WINDOW`` (hidden console) over
        ``DETACHED_PROCESS`` (no console at all). Console *hosts* such as
        ``powershell.exe`` never get going with ``DETACHED_PROCESS``: CI saw the
        helper spawn successfully and then produce no output whatsoever.
        """
        self._reporter.debug("$ (detached) " + " ".join(args))
        flags = 0
        if os.name == "nt":
            if windows_no_window:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            # Popen dup()s the handle into the child, so the parent's copy can be
            # closed here while the child keeps writing to the log.
            with open(log_path, "ab") as out:
                return subprocess.Popen(
                    args,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    creationflags=flags,
                )
        return subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=flags,
        )


def _shutdown_after_interrupt(
    proc: subprocess.Popen,
    reporter: Reporter,
    on_line: Callable[[str], None] | None = None,
) -> None:
    """Reap a child that already received Ctrl-C (same foreground group).

    The child handles SIGINT itself (Minecraft servers save the world and
    exit); this just waits so the CLI returns only once the server is down,
    instead of orphaning it. The stdout pipe is drained on a daemon thread —
    forwarding the child's shutdown output through ``on_line`` — so a verbose
    shutdown can't deadlock on a full pipe. If the child refuses to exit within
    :data:`_INTERRUPT_WAIT`, escalate SIGTERM (JVM runs its shutdown hooks)
    then SIGKILL as a last resort.
    """
    reporter.info("已收到 Ctrl-C,正在等待服务端保存并退出…")
    stdout = proc.stdout
    if stdout is not None:

        def _drain() -> None:
            for line in stdout:
                if on_line:
                    on_line(line.rstrip("\n"))

        # Daemon: dies with the process; blocks until the child exits and
        # closes the pipe, so proc.wait() below can't deadlock.
        threading.Thread(target=_drain, daemon=True).start()
    try:
        proc.wait(timeout=_INTERRUPT_WAIT)
        return
    except subprocess.TimeoutExpired:
        reporter.warn(f"超过 {_INTERRUPT_WAIT:.0f}s 仍未退出,发送终止信号…")
    proc.terminate()
    try:
        proc.wait(timeout=_INTERRUPT_KILL)
    except subprocess.TimeoutExpired:
        reporter.warn("终止信号无效,强制结束进程。")
        proc.kill()
        proc.wait()
