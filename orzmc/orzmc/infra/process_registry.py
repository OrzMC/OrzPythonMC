"""Track running server processes so ``orzmc server --kill-all`` can stop them.

Runs write their PID to ``<root>/run/<label>.pid`` (best-effort); ``--kill-all``
reads them, sends ``SIGTERM`` (``TerminateProcess`` on Windows), and clears the
records. Stale records (process already gone) are removed silently.
"""

from __future__ import annotations

import os
import signal

from orzmc.infra.fs import FileStore

_PID_SUFFIX = ".pid"


class ProcessRegistry:
    """A tiny PID-record store under a directory (one file per running server)."""

    def __init__(self, fs: FileStore, directory: str) -> None:
        self._fs = fs
        self._directory = directory

    def register(self, pid: int, label: str) -> str:
        """Record ``pid`` under ``<directory>/<label>.pid``; returns the path."""
        self._fs.ensure_dir(self._directory)
        path = os.path.join(self._directory, f"{label}{_PID_SUFFIX}")
        self._fs.write_text(path, f"{pid}\n")
        return path

    def unregister(self, path: str) -> None:
        try:
            self._fs.remove(path)
        except OSError:
            pass

    def records(self) -> list[tuple[str, int]]:
        """All live PID records as ``(path, pid)`` (unreadable files are skipped)."""
        if not self._fs.is_dir(self._directory):
            return []
        found: list[tuple[str, int]] = []
        for name in sorted(self._fs.list_dir(self._directory)):
            if not name.endswith(_PID_SUFFIX):
                continue
            path = os.path.join(self._directory, name)
            try:
                pid = int(self._fs.read_text(path).strip())
            except (OSError, ValueError):
                continue
            found.append((path, pid))
        return found

    def kill_all(self) -> list[int]:
        """Terminate every recorded process; returns the PIDs signalled."""
        killed: list[int] = []
        for path, pid in self.records():
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except (ProcessLookupError, PermissionError, OSError):
                # Already gone / not ours: clear the stale record and move on.
                pass
            finally:
                self.unregister(path)
        return killed
