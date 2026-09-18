"""Shared fakes for library tests: no network, no system java, real tmp dirs.

These live in a normal module (not ``conftest``) so test modules can import
them directly. The tests directory is on ``sys.path`` under pytest's default
``prepend`` import mode, so ``from fakes import ...`` resolves here.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, Literal

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
        # 字节任务单独记一份:断言「单文件下载用 start_bytes(带速度/大小),批量用计数」。
        self.byte_starts: list[tuple[str, int | None]] = []
        self.advanced = 0
        self.finishes = 0

    def start(self, desc: str, total: int | None = None) -> None:
        self.starts.append((desc, total))

    def start_bytes(self, desc: str, total: int | None = None) -> None:
        self.byte_starts.append((desc, total))
        self.starts.append((desc, total))

    def advance(self, n: int = 1) -> None:
        self.advanced += n

    def finish(self) -> None:
        self.finishes += 1


class FakeResponse:
    """Minimal response object: ``with`` / ``raise_for_status`` / ``iter_content`` / headers."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status_code = status
        self.headers = headers
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


def parse_range(value: str, size: int) -> tuple[int, int]:
    """``bytes=a-b`` / ``bytes=a-`` → inclusive ``(a, b)`` clamped to ``size``."""
    spec = value.removeprefix("bytes=").partition(",")[0].strip()
    start_text, _, end_text = spec.partition("-")
    start = int(start_text)
    end = size - 1 if not end_text else min(int(end_text), size - 1)
    return start, end


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
        self.redirects: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.json_calls: list[str] = []
        # 覆盖某个 URL 上报的 Content-Length(默认就是字节数;置 None 模拟"服务器不给长度")。
        self.canned_lengths: dict[str, int | None] = {}
        # 每次 download 的关键字参数(timeout / on_chunk / on_open 是否给了),
        # 用来断言「小文件用短超时」「进度总量取自响应头而非 HEAD」。
        self.download_kwargs: list[dict[str, Any]] = []
        # Range 请求记录 + 是否假装服务器不支持 Range(测分块回退)。
        self.range_requests: list[str] = []
        self.ignore_ranges = False

    def head_location(self, url: str) -> str | None:
        """Seeded 302 target for the rate-limit-proof fallback resolver."""
        self.requests.append(("head_location", url))
        return _longest_match(url, self.redirects)

    def get(self, url: str, params=None, headers=None, stream=False, timeout=None) -> Any:
        """Serve ``canned`` bytes, honoring ``Range`` unless ``ignore_ranges`` is set.

        Returns ``FakeResponse``; the return type is ``Any`` because the real
        ``HttpClient.get`` returns a ``requests.Response``.
        """
        self.requests.append(("get", url))
        data = self._payload(url)
        rng = (headers or {}).get("Range")
        if rng and self.ignore_ranges:
            rng = None
        if rng:
            self.range_requests.append(rng)
            start, end = parse_range(rng, len(data))
            body = data[start : end + 1]
            return FakeResponse(
                206,
                body,
                {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Content-Length": str(len(body))},
            )
        length = self.canned_lengths.get(url, len(data))
        return FakeResponse(200, data, {} if length is None else {"Content-Length": str(length)})

    def _payload(self, url: str) -> bytes:
        data = _longest_match(url, self.canned)
        if data is None:
            if self.canned_archive is None:
                raise AssertionError(f"unexpected download: {url}")
            data = self.canned_archive
        return data

    def download(
        self,
        url: str,
        dest_path: str,
        on_chunk: Callable[[int], None] | None = None,
        on_open: Callable[[int | None], None] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> int:
        self.requests.append(("download", url))
        self.download_kwargs.append({"on_chunk": on_chunk, "on_open": on_open, "timeout": timeout})
        data = self._payload(url)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        # 真实客户端从流式 GET 的响应头拿 Content-Length,on_open 在写入前触发。
        if on_open:
            on_open(self.canned_lengths.get(url, len(data)))
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
