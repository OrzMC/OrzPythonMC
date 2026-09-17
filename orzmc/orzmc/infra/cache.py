"""TTL'd JSON cache shared by every remote-metadata adapter.

Mojang's version manifest, fabric-meta, Paper's Fill API and Forge's Maven
promotions all read through this one class, so the whole library follows a
single caching policy:

* a cached JSON is reused while it is younger than ``ttl`` (default 24h);
* ``refresh=True`` — the CLI ``--refresh`` flag — bypasses every cache read;
* when a fetch fails but a copy is on disk (even a stale one), the cached copy
  wins over a hard failure: a slow or temporarily offline network must not
  brick the CLI.

The clock is injected (``now``) so tests never sleep, and the transport /
progress seams live here too, so call sites are a single ``get_json`` call.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.progress import NullProgress, ProgressSink

DEFAULT_TTL = 24 * 60 * 60  # 24h — the one metadata freshness policy
_META_SUBDIR = "meta"
_UNSAFE = frozenset('/\\:*?"<>| ')


def _safe(part: str) -> str:
    """Make one cache-key part portable (no path separators / Windows-reserved chars)."""
    cleaned = "".join("_" if char in _UNSAFE else char for char in part)
    return cleaned or "_"


class MetadataCache:
    """Cached remote-JSON access: fresh cache → no network, ``refresh`` → always network."""

    def __init__(
        self,
        http: HttpClient,
        fs: FileStore,
        cache_dir: str,
        *,
        ttl: float = DEFAULT_TTL,
        refresh: bool = False,
        now: Callable[[], float] = time.time,
        reporter: Reporter | None = None,
        sink: ProgressSink | None = None,
    ) -> None:
        self.http = http
        self.fs = fs
        self.sink = sink or NullProgress()
        self.reporter = reporter or NullReporter()
        self.refresh = refresh
        self._dir = cache_dir
        self._ttl = ttl
        self._now = now

    # ── paths ───────────────────────────────────────────────────────────────

    def meta_path(self, *parts: str) -> str:
        """Cache path for a third-party API response: ``<cache>/meta/<parts>.json``."""
        return os.path.join(self._dir, _META_SUBDIR, *(_safe(part) for part in parts)) + ".json"

    # ── policy ──────────────────────────────────────────────────────────────

    def is_fresh(self, path: str, *, refresh: bool = False) -> bool:
        """True when ``path`` exists and is younger than the TTL.

        A non-positive TTL disables reuse entirely (every read goes to the
        network), which is handy for tests and for "always fresh" callers.
        """
        if refresh or self.refresh or self._ttl <= 0:
            return False
        if not self.fs.is_file(path):
            return False
        age = self._now() - self.fs.mtime(path)
        return 0 <= age < self._ttl

    # ── read / write ────────────────────────────────────────────────────────

    def read(self, path: str) -> Any | None:
        """Fresh cached JSON, or ``None`` when missing / stale / unreadable."""
        if not self.is_fresh(path):
            return None
        return self._read_file(path)

    def write(self, path: str, data: Any) -> None:
        self.fs.write_json(path, data)

    def get_json(
        self,
        path: str,
        url: str,
        *,
        desc: str | None = None,
        params: dict[str, str] | None = None,
        refresh: bool = False,
    ) -> Any:
        """Cached JSON GET: a fresh cache wins, otherwise fetch and store.

        ``desc`` (optional) drives an indeterminate progress line around the
        network call, so a slow API is never a silent wait.
        """
        if not (refresh or self.refresh):
            cached = self.read(path)
            if cached is not None:
                self.reporter.debug(f"使用缓存: {desc or url}")
                return cached
        try:
            data = self._fetch(url, desc, params)
        except Exception as exc:
            stale = self._read_file(path)
            if stale is None:
                raise
            self.reporter.warn(f"{desc or url} 获取失败,回退到本地缓存({exc})")
            return stale
        self.write(path, data)
        return data

    # ── internals ───────────────────────────────────────────────────────────

    def _fetch(self, url: str, desc: str | None, params: dict[str, str] | None) -> Any:
        if desc:
            self.sink.status(desc)
        try:
            return self.http.get_json(url, params=params)
        finally:
            if desc:
                self.sink.finish()

    def _read_file(self, path: str) -> Any | None:
        """Parse a cache file, tolerating corruption (a corrupt cache is a miss)."""
        if not self.fs.is_file(path):
            return None
        try:
            return self.fs.read_json(path)
        except Exception as exc:
            self.reporter.debug(f"缓存损坏,忽略: {path} ({exc})")
            return None
