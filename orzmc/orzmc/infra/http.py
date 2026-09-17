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


def parse_content_length(headers: Mapping[str, str]) -> int | None:
    """Content-Length from response headers, or None when absent/not a number."""
    value = headers.get("Content-Length")
    return int(value) if value and value.isdigit() else None


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
        """
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp = f"{dest_path}.tmp"
        with self.get(url, stream=True, timeout=timeout) as resp:
            if on_open:
                on_open(parse_content_length(resp.headers))
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        if on_chunk:
                            on_chunk(len(chunk))
        os.replace(tmp, dest_path)
        return os.path.getsize(dest_path)
