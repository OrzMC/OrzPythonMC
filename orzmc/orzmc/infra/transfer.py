"""File transfer helpers: byte progress, and ranged parallel download for big files.

``Downloader``, ``JavaEnv`` and the Mojang metadata download all funnel through
this module, so byte progress is rendered identically everywhere.

**Why ranged parallel download exists**: a single TCP stream over a lossy or
proxied path is limited by window and packet loss (measured on a proxied link:
39MB client jar at 2.5 MB/s single stream vs 4.0 MB/s with 4 ranged streams).
Thousands of *small* files are a different problem — there the cost is
per-request latency, so the fix is concurrency
(``RuntimeOptions.download_threads``). Hence two knobs: ``workers`` for the
many-small-files phase, ``parts`` for the few-big-files phase.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from orzmc.infra.http import HttpClient
from orzmc.infra.progress import ProgressSink

# 只有大文件才值得分块:对「几千个小文件」分块毫无帮助(那是并发的问题),只会白加请求数。
PARALLEL_MIN_SIZE = 8 * 1024 * 1024
# 实测 4 路就够了:8 路没有额外收益(上游 CDN 或本机代理更早成为瓶颈)。
PARALLEL_PARTS = 4

_STREAM_CHUNK = 1024 * 64
# 第一块就地取,顺便当「服务器是否支持 Range」的探针:它本身是真活,不是浪费的探测请求。
# 只让这一小块串行,剩下的大头并行,并行度几乎不受影响。
_PROBE_SIZE = 1024 * 1024
# 分块粒度下限:块太小则请求开销/拼接收益都不划算。
_MIN_PART_SIZE = PARALLEL_MIN_SIZE // 4
# 进度泵刷新间隔:工作线程只做原子累加,``sink`` 始终只由调用线程触碰(避免多线程渲染)。
_PROGRESS_TICK = 0.1


class _ByteCounter:
    """Thread-safe byte accumulator (workers add, the calling thread drains)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def add(self, n: int) -> None:
        with self._lock:
            self._value += n

    def drain(self) -> int:
        with self._lock:
            value, self._value = self._value, 0
            return value


def download_with_progress(
    http: HttpClient,
    url: str,
    dest: str,
    sink: ProgressSink,
    desc: str,
    timeout: float | tuple[float, float] | None = None,
    parts: int = 1,
) -> int:
    """Stream ``url`` to ``dest``, reporting byte progress to ``sink``.

    Returns the number of bytes written. ``total`` is unknown when the server
    omits ``Content-Length`` — the progress line then shows a pulsing bar
    instead of a percentage, which still tells the user work is happening.

    The size comes from the streaming GET response, so no HEAD probe is issued:
    on a high-latency link that probe costs a whole extra round trip per file.

    ``parts > 1`` splits files of at least ``PARALLEL_MIN_SIZE`` into that many
    ranged streams (see :func:`_download_ranged`); anything else — and any
    server that ignores ``Range`` — falls back to a single stream.
    """
    if parts > 1:
        return _download_ranged(http, url, dest, sink, desc, timeout, parts)
    try:
        return http.download(
            url,
            dest,
            on_chunk=lambda n: sink.advance(n),
            on_open=lambda total: sink.start_bytes(desc, total),
            timeout=timeout,
        )
    finally:
        sink.finish()


def _download_ranged(
    http: HttpClient,
    url: str,
    dest: str,
    sink: ProgressSink,
    desc: str,
    timeout: float | tuple[float, float] | None,
    parts: int,
) -> int:
    """Fetch the first chunk inline, then the rest as parallel ranged streams.

    That first ranged request doubles as the Range-support probe, so it costs
    nothing extra. When the server ignores ``Range`` (proxies and some mirrors
    do) the response *is* the whole body: we keep streaming it, which also
    avoids the failure mode where every worker downloads the complete file.
    """
    tmp = f"{dest}.tmp"
    part_paths: list[str] = []
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with http.get(url, stream=True, timeout=timeout, headers={"Range": f"bytes=0-{_PROBE_SIZE - 1}"}) as resp:
            total = _total_size(resp.status_code, resp.headers)
            if resp.status_code != 206 or total is None:
                return _stream_body(resp, dest, sink, desc, total)
            sink.start_bytes(desc, total)
            part_paths.append(f"{dest}.part0")
            with open(part_paths[0], "wb") as f:
                first_len = _copy_chunks(resp, f, sink.advance)

        remaining = total - first_len
        if remaining > 0:
            jobs = _range_jobs(first_len, total, min(parts, max(1, remaining // _MIN_PART_SIZE)))
            part_paths.extend(f"{dest}.part{index + 1}" for index in range(len(jobs)))
            counter = _ByteCounter()
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = [
                    pool.submit(_fetch_range, http, url, start, end, path, counter, timeout)
                    for (start, end), path in zip(jobs, part_paths[1:], strict=True)
                ]
                _pump(sink, counter, futures)
                for future in futures:
                    future.result()  # re-raise the first failure
            sink.advance(counter.drain())

        written = _assemble(part_paths, tmp)
        if written != total:
            raise RuntimeError(f"分块下载拼接后大小不符: 期望 {total} 字节,实际 {written}")
        os.replace(tmp, dest)
        return written
    finally:
        for path in [*part_paths, tmp]:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        sink.finish()


def _range_jobs(first_len: int, total: int, count: int) -> list[tuple[int, int]]:
    """Split ``[first_len, total-1]`` into ``count`` contiguous byte ranges."""
    jobs: list[tuple[int, int]] = []
    chunk = (total - first_len) // count
    start = first_len
    for index in range(count):
        end = total - 1 if index == count - 1 else start + chunk - 1
        jobs.append((start, end))
        start = end + 1
    return jobs


def _total_size(status: int, headers: Mapping[str, str]) -> int | None:
    """Resource size from a ``Content-Range`` (206) or ``Content-Length`` (200) response."""
    if status == 206:
        _, _, total = (headers.get("Content-Range") or "").partition("/")
        return int(total) if total.isdigit() else None
    value = headers.get("Content-Length")
    return int(value) if value and value.isdigit() else None


def _copy_chunks(resp: Any, out: Any, on_bytes: Callable[[int], None]) -> int:
    """Write a response body to ``out``, reporting each chunk's size. Returns bytes written."""
    written = 0
    for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
        if chunk:
            out.write(chunk)
            written += len(chunk)
            on_bytes(len(chunk))
    return written


def _pump(sink: ProgressSink, counter: _ByteCounter, futures: list[Future[None]]) -> None:
    """Feed progress to ``sink`` from the calling thread until every part is done."""
    while True:
        done = all(future.done() for future in futures)
        sink.advance(counter.drain())
        if done:
            return
        time.sleep(_PROGRESS_TICK)


def _fetch_range(
    http: HttpClient,
    url: str,
    start: int,
    end: int,
    path: str,
    counter: _ByteCounter,
    timeout: float | tuple[float, float] | None,
) -> None:
    with http.get(url, stream=True, timeout=timeout, headers={"Range": f"bytes={start}-{end}"}) as resp:
        if resp.status_code != 206:
            raise RuntimeError(f"分块下载被拒绝 (HTTP {resp.status_code}): {url}")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
                if chunk:
                    f.write(chunk)
                    counter.add(len(chunk))


def _stream_body(resp: Any, dest: str, sink: ProgressSink, desc: str, total: int | None) -> int:
    """Fallback: write an already-open response body to ``dest`` (Range was ignored)."""
    sink.start_bytes(desc, total)
    tmp = f"{dest}.tmp"
    with open(tmp, "wb") as f:
        written = _copy_chunks(resp, f, sink.advance)
    os.replace(tmp, dest)
    return written


def _assemble(part_paths: list[str], tmp: str) -> int:
    written = 0
    with open(tmp, "wb") as out:
        for path in part_paths:
            with open(path, "rb") as part:
                shutil.copyfileobj(part, out, _STREAM_CHUNK)
            written += os.path.getsize(path)
    return written
