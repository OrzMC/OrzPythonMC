"""HTTP client with retries, timeouts and atomic file downloads."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 连接超时与读取超时分开:读取超时是**单次 socket 读**的上限(不是整个响应的耗时),
# 所以慢速大文件不会被它掐断,而卡死的连接又能较快被判死。
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
# 批量下载几千个小文件时,一个卡住的连接不值得等 30s:读超时短一点,失败后 urllib3 会
# 用新连接重试。实测瓶颈正是「每请求延迟 乘 并发度」,快速失败重试比久等更划算。
SMALL_FILE_READ_TIMEOUT = 15.0

# keep-alive 连接池上限必须 ≥ 下载并发数:否则 urllib3 会丢弃超出上限的连接、每个请求
# 重新 TCP+TLS。慢速链路上一次握手实测 1.7-3.1s,而复用连接后同一请求只要 1.17s。
DEFAULT_POOL_SIZE = 32

_CHUNK_SIZE = 1024 * 64

# 流式 body 读取阶段的中途断连(代理重置 / 丢包)是 urllib3 ``Retry`` 覆盖不到的:
# ``Retry`` 只管建立响应(urlopen),不管 ``iter_content`` 逐块读取。实测在这条代理
# 链路上随机出现 ``Connection broken: IncompleteRead`` —— 一个 40MB 文件读到 1.8MB
# 掉线就整份重来。这里自己重试,并用 ``Range`` 从已落盘字节续传。
RESUME_ATTEMPTS = 4


def parse_content_length(headers: Mapping[str, str]) -> int | None:
    """Content-Length from response headers, or None when absent/not a number."""
    value = headers.get("Content-Length")
    return int(value) if value and value.isdigit() else None


def parse_resource_size(status: int, headers: Mapping[str, str]) -> int | None:
    """资源总大小:206 取 ``Content-Range`` 的分母,否则取 ``Content-Length``。"""
    if status == 206:
        _, _, total = (headers.get("Content-Range") or "").partition("/")
        return int(total) if total.isdigit() else None
    return parse_content_length(headers)


def _range_header(offset: int, end: int | None) -> dict[str, str]:
    return {"Range": f"bytes={offset}-{end}" if end is not None else f"bytes={offset}-"}


class HttpClient:
    """Thin wrapper over requests.Session with retry policy and atomic downloads.

    ``timeout`` is either a single value (connect + read) or ``(connect, read)``.
    Individual calls may override it, which is how the downloader applies a
    tighter budget to the thousands of small asset files than to big ones.
    """

    def __init__(
        self,
        timeout: float | tuple[float, float] = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
        retries: int = 3,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._timeout = timeout
        self.pool_size = pool_size
        self._session = self._make_session(retries, pool_size)

    @staticmethod
    def _make_session(retries: int, pool_size: int) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
        timeout: float | tuple[float, float] | None = None,
    ) -> requests.Response:
        resp = self._session.get(
            url,
            params=params,
            headers=headers,
            stream=stream,
            timeout=self._timeout if timeout is None else timeout,
        )
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
        return self.get(url, params=params, headers=headers).json()

    def get_text(self, url: str, params: dict[str, str] | None = None) -> str:
        return self.get(url, params=params).text

    def head_location(self, url: str) -> str | None:
        """Return the ``Location`` a URL redirects to, without following it.

        Used as a rate-limit-proof fallback: ``github.com/<repo>/releases/latest``
        answers 302 to ``.../releases/tag/<tag>`` from a non-API endpoint
        (the REST API is limited to 60 requests/hour/IP).
        """
        try:
            resp = self._session.head(url, timeout=self._timeout, allow_redirects=False)
        except requests.RequestException:
            return None
        return resp.headers.get("Location")

    def download(
        self,
        url: str,
        dest_path: str,
        on_chunk: Callable[[int], None] | None = None,
        on_open: Callable[[int | None], None] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> int:
        """Stream ``url`` to ``dest_path`` atomically (tmp file + rename).

        Returns the number of bytes written. ``on_chunk`` (optional) is called
        with the number of bytes of each chunk for progress reporting.

        ``on_open`` (optional) is called once the response headers are in, with
        the ``Content-Length`` (or None) — the streaming GET itself carries the
        size, so no probing HEAD request is needed (one saved round trip per
        file, which is seconds on a high-latency link).

        A mid-stream connection break is retried and *resumed* (see
        :meth:`stream_range`), so a 40MB file that dies at 39MB only fetches the
        last megabyte again.
        """
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        tmp = f"{dest_path}.tmp"
        self.stream_range(url, 0, None, tmp, on_chunk=on_chunk, on_open=on_open, timeout=timeout)
        os.replace(tmp, dest_path)
        return os.path.getsize(dest_path)

    def stream_range(
        self,
        url: str,
        start: int,
        end: int | None,
        dest_path: str,
        on_chunk: Callable[[int], None] | None = None,
        on_open: Callable[[int | None], None] | None = None,
        timeout: float | tuple[float, float] | None = None,
        attempts: int = RESUME_ATTEMPTS,
    ) -> tuple[int, int | None, int]:
        """流式写入**闭区间**字节范围 ``[start, end]`` 到 ``dest_path``,中途断连可续传。

        返回 ``(写入字节数, 资源总大小, 首个响应的状态码)``;``end=None`` 表示到文件末尾。
        断连(底层抛 ``requests.RequestException``)后,下一次用
        ``Range: bytes=<已落盘>-<end>`` 只补齐剩余部分;若服务器忽略 ``Range`` 回 200,
        则作废已落盘字节整份重来。首块(未落盘)且非强制 Range 时不发 ``Range``,
        以便从 200 的 ``Content-Length`` 得到总大小。
        """
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        expected = None if end is None else end - start + 1
        have = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
        if expected is not None:
            have = min(have, expected)
        total: int | None = None
        status = 0
        last: Exception | None = None
        for attempt in range(attempts):
            offset = start + have
            headers = None if offset == 0 and not expected else _range_header(offset, end)
            try:
                with self.get(url, stream=True, timeout=timeout, headers=headers) as resp:
                    if attempt == 0:
                        status = resp.status_code
                        total = parse_resource_size(resp.status_code, resp.headers)
                        if on_open:
                            on_open(total)
                    if resp.status_code == 200 and offset > 0:
                        have = 0  # 服务器忽略 Range:已落盘字节作废,整份重来
                    with open(dest_path, "ab" if have else "wb") as f:
                        for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                have += len(chunk)
                                if on_chunk:
                                    on_chunk(len(chunk))
                return have, total, status
            except requests.RequestException as exc:
                last = exc
                continue
        raise RuntimeError(f"下载中途多次断开,已重试 {attempts} 次仍失败: {url}") from last

    def fetch_range(
        self,
        url: str,
        start: int,
        end: int,
        dest_path: str,
        on_chunk: Callable[[int], None] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> int:
        """下载闭区间 ``[start, end]`` 到 ``dest_path``(必须 206;分块下载用)。

        与 :meth:`stream_range` 同样支持中途断连续传;服务器返回 200(忽略 ``Range``)
        说明该分块无法按范围取,明确报错而不写坏文件。
        """
        written, _, status = self.stream_range(url, start, end, dest_path, on_chunk=on_chunk, timeout=timeout)
        if status != 206:
            raise RuntimeError(f"分块下载被拒绝 (HTTP {status}): {url}")
        return written
