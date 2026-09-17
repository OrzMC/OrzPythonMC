"""HTTP client with retries, timeouts and atomic file downloads."""

from __future__ import annotations

import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HttpClient:
    """Thin wrapper over requests.Session with retry policy and atomic downloads."""

    def __init__(self, timeout: float = 30.0, retries: int = 3) -> None:
        self._timeout = timeout
        self._session = self._make_session(retries)

    @staticmethod
    def _make_session(retries: int) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        resp = self._session.get(url, params=params, headers=headers, stream=stream, timeout=self._timeout)
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
        return self.get(url, params=params, headers=headers).json()

    def get_text(self, url: str, params: dict[str, str] | None = None) -> str:
        return self.get(url, params=params).text

    def content_length(self, url: str) -> int | None:
        """Return the Content-Length of ``url`` via HEAD, or None when unknown."""
        try:
            resp = self._session.head(url, timeout=self._timeout, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException:
            return None
        value = resp.headers.get("Content-Length")
        return int(value) if value and value.isdigit() else None

    def download(self, url: str, dest_path: str, on_chunk: Any = None) -> int:
        """Stream ``url`` to ``dest_path`` atomically (tmp file + rename).

        Returns the number of bytes written. ``on_chunk`` (optional) is called
        with the number of bytes of each chunk for progress reporting.
        """
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp = f"{dest_path}.tmp"
        with self.get(url, stream=True) as resp, open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
                    if on_chunk:
                        on_chunk(len(chunk))
        os.replace(tmp, dest_path)
        return os.path.getsize(dest_path)
