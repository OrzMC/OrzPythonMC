"""Hangar (hangar.papermc.io) adapter: search projects and resolve PAPER downloads.

Hangar's v1 API powers the ``orzmc plugin`` command: ``/projects`` search,
``/projects/{slug}/latest`` (plain-text release name) and
``/projects/{slug}/versions/{version}`` (per-platform download URL).
"""

from __future__ import annotations

from typing import Any

from orzmc.infra.http import HttpClient

HANGAR_BASE = "https://hangar.papermc.io/api/v1"
PLATFORM = "PAPER"
#: Hangar 相对下载路径(以 ``/`` 开头)需要补全的前缀。
HANGAR_SITE = "https://hangar.papermc.io"


class Hangar:
    """Thin Hangar v1 client over the shared :class:`HttpClient`."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def search(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        """Search projects by name; returns the raw project objects."""
        data = self._http.get_json(
            f"{HANGAR_BASE}/projects",
            params={"q": text, "limit": str(limit), "platform": PLATFORM},
        )
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, list) else []

    def latest_release(self, slug: str) -> str | None:
        """Newest release version name for ``slug`` (plain text), or None."""
        try:
            text = self._http.get_text(f"{HANGAR_BASE}/projects/{slug}/latest", params={"channel": "Release"})
        except Exception:
            return None
        return text.strip() or None

    def download(self, slug: str, version: str | None = None) -> tuple[str, str] | None:
        """``(file_name, url)`` for the latest (or given) PAPER release; None if absent."""
        resolved = version or self.latest_release(slug)
        if not resolved:
            return None
        data = self._http.get_json(f"{HANGAR_BASE}/projects/{slug}/versions/{resolved}")
        if not isinstance(data, dict):
            return None
        downloads = data.get("downloads") or {}
        paper = downloads.get(PLATFORM) or {}
        url = paper.get("downloadUrl") or paper.get("externalUrl")
        if not url:
            return None
        url = str(url)
        if url.startswith("/"):
            url = HANGAR_SITE + url
        name = (paper.get("fileInfo") or {}).get("name") or f"{slug}-{resolved}.jar"
        return str(name), url
