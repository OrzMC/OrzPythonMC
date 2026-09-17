"""Shared fakes for library tests: no network, no system java, real tmp dirs.

These live in a normal module (not ``conftest``) so test modules can import
them directly. The tests directory is on ``sys.path`` under pytest's default
``prepend`` import mode, so ``from fakes import ...`` resolves here.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, NoReturn

from orzmc.infra.http import HttpClient
from orzmc.infra.log import Reporter
from orzmc.infra.progress import ProgressSink
from orzmc.infra.runner import ProcessRunner


class FakeReporter(Reporter):
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _add(self, level: str, text: str) -> None:
        self.lines.append((level, text))

    def debug(self, text: str) -> None:
        self._add("debug", text)

    def info(self, text: str) -> None:
        self._add("info", text)

    def warn(self, text: str) -> None:
        self._add("warn", text)

    def error(self, text: str) -> None:
        self._add("error", text)

    def success(self, text: str) -> None:
        self._add("success", text)

    def plain(self, text: str) -> None:
        self._add("plain", text)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.lines]


class FakeSink(ProgressSink):
    def __init__(self) -> None:
        self.starts: list[tuple[str, int | None]] = []
        self.advanced = 0

    def start(self, desc: str, total: int | None = None) -> None:
        self.starts.append((desc, total))

    def advance(self, n: int = 1) -> None:
        self.advanced += n

    def finish(self) -> None:
        pass


class FakeHttp(HttpClient):
    """Rejects any real network access; tests seed responses per URL.

    ``json_responses`` maps a URL substring to a JSON value; ``canned`` maps a
    URL substring to raw download bytes; ``canned_archive`` remains a fallback
    for single-download tests. Subclasses ``HttpClient`` (without its session)
    so mypy treats it as the real type; any method added to ``HttpClient`` must
    be overridden here.
    """

    def __init__(self) -> None:
        self.canned_archive: bytes | None = None
        self.canned: dict[str, bytes] = {}
        self.json_responses: dict[str, Any] = {}
        self.requests: list[tuple[str, str]] = []
        self.json_calls: list[str] = []

    def content_length(self, url: str) -> int | None:
        return None

    def get(self, url: str, params=None, headers=None, stream=False) -> NoReturn:
        raise AssertionError(f"unexpected get: {url}")

    def download(self, url: str, dest_path: str, on_chunk=None) -> int:
        self.requests.append(("download", url))
        data = _longest_match(url, self.canned)
        if data is None:
            if self.canned_archive is None:
                raise AssertionError(f"unexpected download: {url}")
            data = self.canned_archive
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        if on_chunk:
            on_chunk(len(data))
        return len(data)

    def get_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.json_calls.append(url)
        value = _longest_match(url, self.json_responses)
        if value is None:
            raise AssertionError(f"unexpected get_json: {url}")
        return value

    def get_text(self, url: str, params: dict[str, str] | None = None):
        raise AssertionError(f"unexpected get_text: {url}")


class FakeProcess(ProcessRunner):
    """Records subprocess commands and, optionally, creates files after each run.

    ``created`` lists absolute paths the fake writes after ``run_stream`` — this
    simulates an installer producing its artifacts, so providers that verify a
    product file exists (forge shim, fabric-server-launch.jar) work offline.
    """

    def __init__(self, code: int = 0, created: list[str] | None = None) -> None:
        self.code = code
        self.calls: list[list[str]] = []
        self.detached: list[list[str]] = []
        self.created = created or []

    def run_stream(self, cmd: list[str], on_line=None, cwd: str | None = None) -> int:
        self.calls.append(list(cmd))
        for path in self.created:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("product")
        if on_line:
            for line in self.created:
                on_line("created " + line)
        return self.code

    def run_detached(
        self,
        args: list[str],
        cwd: str | None = None,
        log_path: str | None = None,
        *,
        windows_no_window: bool = False,
    ) -> Any:
        self.detached.append(list(args))
        return SimpleNamespace(pid=4242)

    @property
    def last_cmd(self) -> list[str] | None:
        return self.calls[-1] if self.calls else None


def _longest_match(url: str, table: dict[str, Any]) -> Any | None:
    """Return the value whose key is the longest substring of ``url``."""
    best = max((key for key in table if key in url), key=len, default=None)
    return table.get(best) if best is not None else None
