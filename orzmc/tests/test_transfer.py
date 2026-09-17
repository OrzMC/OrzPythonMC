"""传输层与进度:总量取自流式响应(不再 HEAD)、失败也收尾、超时可逐次覆盖。

``content_length()`` 那个探测用的 HEAD 请求已删除:每个单文件一次多出的往返,
在实测的高延迟链路上要 0.5-3s,而这些文件本来就在流式 GET 的响应头里带长度。
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar, Literal, cast

import pytest
from fakes import FakeHttp, parse_range
from requests.adapters import HTTPAdapter

from orzmc.infra.http import HttpClient, parse_content_length
from orzmc.infra.transfer import _PROBE_SIZE, PARALLEL_MIN_SIZE, download_with_progress

# 略高于分块阈值:够触发分块,又不至于让测试变慢。
BIG_SIZE = PARALLEL_MIN_SIZE + 1024


class _RangeHandler(BaseHTTPRequestHandler):
    """loopback 服务器:支持 Range(206 + Content-Range),可切换为忽略 Range。"""

    payload: ClassVar[bytes] = bytes(range(256)) * (BIG_SIZE // 256 + 1)
    ignore_ranges: ClassVar[bool] = False

    def do_GET(self) -> None:
        data = type(self).payload
        requested = self.headers.get("Range")
        if requested and not type(self).ignore_ranges:
            start, end = parse_range(requested, len(data))
            body = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def loopback_big_file() -> Any:
    """本机 loopback HTTP 服务(不碰外部网络),提供一个支持 Range 的大文件。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/big.bin"
    finally:
        server.shutdown()
        server.server_close()


class TestParseContentLength:
    def test_missing_header_is_none(self) -> None:
        assert parse_content_length({}) is None

    def test_non_numeric_header_is_none(self) -> None:
        assert parse_content_length({"Content-Length": "chunked"}) is None

    def test_numeric_header_is_parsed(self) -> None:
        assert parse_content_length({"Content-Length": "4096"}) == 4096


class TestDownloadWithProgress:
    def test_total_comes_from_the_streaming_response(self, tmp_path, sink) -> None:
        http = FakeHttp()
        http.canned = {"https://x/f.bin": b"1234567"}
        written = download_with_progress(http, "https://x/f.bin", str(tmp_path / "f.bin"), sink, "下载东西")
        assert written == 7
        assert sink.starts == [("下载东西", 7)]
        assert sink.advanced == 7
        assert sink.finishes == 1
        # 只有一次流式 GET:没有多余的 HEAD 探测
        assert [kind for kind, _ in http.requests] == ["download"]

    def test_unknown_length_still_reports_progress(self, tmp_path, sink) -> None:
        """服务器不给 Content-Length → 总量 None(呼吸条),但字节仍然在推进。"""
        http = FakeHttp()
        http.canned = {"https://x/f.bin": b"abc"}
        http.canned_lengths["https://x/f.bin"] = None
        download_with_progress(http, "https://x/f.bin", str(tmp_path / "f.bin"), sink, "无长度")
        assert sink.starts == [("无长度", None)]
        assert sink.advanced == 3

    def test_finish_runs_even_when_download_fails(self, tmp_path, sink) -> None:
        http = FakeHttp()  # 没种响应 → 下载抛错
        with pytest.raises(AssertionError):
            download_with_progress(http, "https://x/boom", str(tmp_path / "f.bin"), sink, "失败")
        assert sink.finishes == 1


class _StubResponse:
    """可供 ``with`` 使用的最小响应(不联网)。"""

    headers: ClassVar[dict[str, str]] = {"Content-Length": "5"}
    status_code = 200

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int) -> object:
        return iter([b"abc", b"de"])


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _StubResponse:
        self.calls.append({"url": url, **kwargs})
        return _StubResponse()


class TestHttpClient:
    def test_pool_matches_the_requested_concurrency(self) -> None:
        # 回归:HTTPAdapter 默认 pool_maxsize=10 —— 并发高于它时 urllib3 会丢弃
        # keep-alive 连接、每个请求重新 TCP+TLS(实测握手 1.7-3.1s,是复用连接的 3 倍)。
        client = HttpClient(pool_size=48)
        adapter = cast(HTTPAdapter, client._session.get_adapter("https://"))
        assert client.pool_size == 48
        assert adapter._pool_maxsize == 48
        assert adapter._pool_connections == 48

    def test_download_honours_a_per_call_timeout(self, tmp_path) -> None:
        client = HttpClient(timeout=(9.0, 9.0))
        session = _RecordingSession()
        client._session = session  # type: ignore[assignment]
        client.download("https://x/f", str(tmp_path / "f"), timeout=(1.0, 2.0))
        assert session.calls[0]["timeout"] == (1.0, 2.0)
        assert session.calls[0]["stream"] is True

    def test_download_uses_the_client_default_timeout(self, tmp_path) -> None:
        client = HttpClient(timeout=(9.0, 9.0))
        session = _RecordingSession()
        client._session = session  # type: ignore[assignment]
        client.download("https://x/f", str(tmp_path / "f"))
        assert session.calls[0]["timeout"] == (9.0, 9.0)

    def test_download_is_atomic_and_reports_before_chunks(self, tmp_path) -> None:
        client = HttpClient()
        client._session = _RecordingSession()  # type: ignore[assignment]
        dest = str(tmp_path / "f.bin")
        events: list[tuple[str, object]] = []
        size = client.download(
            "https://x/f",
            dest,
            on_chunk=lambda n: events.append(("chunk", n)),
            on_open=lambda total: events.append(("open", total)),
        )
        assert size == 5
        assert events == [("open", 5), ("chunk", 3), ("chunk", 2)]
        assert not (tmp_path / "f.bin.tmp").exists()


# ── 大文件分块并行(批次 2)────────────────────────────────────────────────────
#
# 单流吞吐受路径丢包/窗口限制(实测 39MB 客户端 jar 2.5 MB/s,4 路 Range 4.0 MB/s)。
# 分块只对大文件有意义:小文件走的是并发(``download_threads``),不是分块。


class _SlowRangeHttp(FakeHttp):
    """记录同时在跑的 Range 请求数 —— 用来证明分块真的并行。"""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def get(self, url, params=None, headers=None, stream=False, timeout=None):
        if not (headers or {}).get("Range"):
            return super().get(url, params, headers, stream, timeout)
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.02)
            return super().get(url, params, headers, stream, timeout)
        finally:
            with self._lock:
                self.active -= 1


class _FailingRangeHttp(FakeHttp):
    """第一个分块之后的分块请求全部失败(测失败清理)。"""

    def get(self, url, params=None, headers=None, stream=False, timeout=None):
        if (headers or {}).get("Range", "").startswith(f"bytes={_PROBE_SIZE}-"):
            raise RuntimeError("boom")
        return super().get(url, params, headers, stream, timeout)


def _big_payload() -> bytes:
    return bytes(range(256)) * (BIG_SIZE // 256 + 1)


class TestParallelDownload:
    def test_large_file_is_split_into_ranges_and_assembled(self, tmp_path, sink) -> None:
        payload = _big_payload()
        http = FakeHttp()
        http.canned = {"https://big/f.bin": payload}
        written = download_with_progress(http, "https://big/f.bin", str(tmp_path / "f.bin"), sink, "大文件", parts=4)
        assert written == len(payload)
        assert (tmp_path / "f.bin").read_bytes() == payload
        assert sink.starts == [("大文件", len(payload))]
        # 1 个探针/首块 + 3 个并行分块(8MB 文件按 _MIN_PART_SIZE 切成 3 块)
        assert len(http.range_requests) == 4
        assert not list(tmp_path.glob("f.bin.part*"))
        assert not (tmp_path / "f.bin.tmp").exists()

    def test_ranges_run_in_parallel(self, tmp_path, sink) -> None:
        http = _SlowRangeHttp()
        http.canned = {"https://big/f.bin": _big_payload()}
        download_with_progress(http, "https://big/f.bin", str(tmp_path / "f.bin"), sink, "大文件", parts=4)
        assert http.peak >= 2  # 没有并行时只会是 1

    def test_small_file_is_not_split(self, tmp_path, sink) -> None:
        http = FakeHttp()
        http.canned = {"https://small/f.bin": b"y" * 4096}
        written = download_with_progress(http, "https://small/f.bin", str(tmp_path / "f.bin"), sink, "小文件", parts=4)
        assert written == 4096
        assert (tmp_path / "f.bin").read_bytes() == b"y" * 4096
        assert len(http.range_requests) == 1  # 只有首块请求,没有额外分块

    def test_ignored_range_falls_back_to_one_stream(self, tmp_path, sink) -> None:
        # 代理/镜像可能忽略 Range:此时探针响应本身就是完整正文 —— 必须直接写下去,
        # 否则每个分块都会各下一份完整文件。
        payload = _big_payload()
        http = FakeHttp()
        http.ignore_ranges = True
        http.canned = {"https://big/f.bin": payload}
        written = download_with_progress(http, "https://big/f.bin", str(tmp_path / "f.bin"), sink, "大文件", parts=4)
        assert written == len(payload)
        assert (tmp_path / "f.bin").read_bytes() == payload
        assert http.range_requests == []
        assert [kind for kind, _ in http.requests] == ["get"]  # 只发了一次请求

    def test_failed_part_cleans_up_and_raises(self, tmp_path, sink) -> None:
        http = _FailingRangeHttp()
        http.canned = {"https://big/f.bin": _big_payload()}
        with pytest.raises(RuntimeError):
            download_with_progress(http, "https://big/f.bin", str(tmp_path / "f.bin"), sink, "大文件", parts=4)
        assert not (tmp_path / "f.bin").exists()
        assert not list(tmp_path.glob("f.bin.part*"))
        assert not (tmp_path / "f.bin.tmp").exists()

    def test_parts_one_uses_the_plain_download_path(self, tmp_path, sink) -> None:
        http = FakeHttp()
        http.canned = {"https://big/f.bin": b"z" * 4096}
        download_with_progress(http, "https://big/f.bin", str(tmp_path / "f.bin"), sink, "单流", parts=1)
        assert http.range_requests == []
        assert [kind for kind, _ in http.requests] == ["download"]


class TestParallelDownloadOverRealHttp:
    """:class:`HttpClient` + 本地 loopback 服务器:验证真实 Range 协议(不出本机)。"""

    def test_ranged_download_matches_the_payload(self, loopback_big_file, tmp_path, sink) -> None:
        http = HttpClient()
        dest = tmp_path / "f.bin"
        written = download_with_progress(http, loopback_big_file, str(dest), sink, "大文件", parts=4)
        assert written == len(_RangeHandler.payload)
        assert dest.read_bytes() == _RangeHandler.payload
        assert sink.starts == [("大文件", len(_RangeHandler.payload))]
        assert not list(tmp_path.glob("f.bin.part*"))

    def test_ignored_range_falls_back_over_real_http(self, loopback_big_file, tmp_path, sink, monkeypatch) -> None:
        monkeypatch.setattr(_RangeHandler, "ignore_ranges", True)
        dest = tmp_path / "f.bin"
        written = download_with_progress(HttpClient(), loopback_big_file, str(dest), sink, "大文件", parts=4)
        assert written == len(_RangeHandler.payload)
        assert dest.read_bytes() == _RangeHandler.payload
