"""传输层与进度:总量取自流式响应(不再 HEAD)、失败也收尾、超时可逐次覆盖。

``content_length()`` 那个探测用的 HEAD 请求已删除:每个单文件一次多出的往返,
在实测的高延迟链路上要 0.5-3s,而这些文件本来就在流式 GET 的响应头里带长度。
"""

from __future__ import annotations

from typing import ClassVar, Literal, cast

import pytest
from fakes import FakeHttp
from requests.adapters import HTTPAdapter

from orzmc.infra.http import HttpClient, parse_content_length
from orzmc.infra.transfer import download_with_progress


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
