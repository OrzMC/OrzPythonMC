"""Metadata caching policy: 24h TTL, ``--refresh`` bypass, stale fallback.

The clock is injected, so no test ever sleeps and no test touches the network
(``FakeHttp`` raises on any unexpected request).
"""

from __future__ import annotations

import os
import time

import pytest
from fakes import FakeHttp, FakeReporter, FakeSink

from orzmc.core.fabric import Fabric
from orzmc.core.forge import PROMOTIONS_URL, Forge
from orzmc.core.mojang import VERSION_MANIFEST_URL, Mojang
from orzmc.core.server.paper import PaperAPI
from orzmc.domain.paths import PathLayout
from orzmc.infra.cache import DEFAULT_TTL, MetadataCache
from orzmc.infra.fs import FileStore

MANIFEST = {"versions": [{"id": "1.20.4", "type": "release", "url": "https://meta/1.20.4.json"}]}


class OfflineHttp(FakeHttp):
    """FakeHttp whose metadata requests always fail (simulated outage)."""

    def get_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.json_calls.append(url)
        raise ConnectionError("network down")


def _cache(
    tmp_path, http: FakeHttp, *, ttl: float = DEFAULT_TTL, refresh: bool = False, now=None, reporter=None, sink=None
) -> MetadataCache:
    return MetadataCache(
        http,
        FileStore(),
        PathLayout(root=str(tmp_path)).cache_dir(),
        ttl=ttl,
        refresh=refresh,
        now=now or time.time,
        reporter=reporter or FakeReporter(),
        sink=sink or FakeSink(),
    )


class TestCachePolicy:
    def test_fresh_cache_avoids_network(self, tmp_path) -> None:
        http = FakeHttp()  # no responses seeded: any request fails the test
        cache = _cache(tmp_path, http)
        cache.write(cache.meta_path("paper-project"), {"versions": {}})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {"versions": {}}
        assert http.json_calls == []

    def test_fresh_cache_avoids_network_for_manifest(self, tmp_path) -> None:
        http = FakeHttp()
        cache = _cache(tmp_path, http)
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path))
        fs.write_json(paths.version_manifest_path(), MANIFEST)
        mojang = Mojang(cache, paths.version_manifest_path(), paths.version_jsons_dir())
        assert mojang.release_version_ids() == ["1.20.4"]
        assert http.json_calls == []

    def test_expired_cache_refetches(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"versions": {"1.20": ["1.20.4"]}}}
        fs = FileStore()
        cache_dir = PathLayout(root=str(tmp_path)).cache_dir()
        fs.write_json(os.path.join(cache_dir, "meta", "paper-project.json"), {"stale": True})
        future = time.time() + DEFAULT_TTL + 1
        cache = MetadataCache(http, fs, cache_dir, now=lambda: future)
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {
            "versions": {"1.20": ["1.20.4"]}
        }
        assert http.json_calls == ["https://fill/paper"]

    def test_refresh_bypasses_fresh_cache(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"versions": {"new": ["x"]}}}
        cache = _cache(tmp_path, http, refresh=True)
        cache.write(cache.meta_path("paper-project"), {"versions": {"old": ["y"]}})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {"versions": {"new": ["x"]}}

    def test_per_call_refresh_overrides_fresh_cache(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"fresh": True}}
        cache = _cache(tmp_path, http)
        cache.write(cache.meta_path("paper-project"), {"old": True})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper", refresh=True) == {"fresh": True}

    def test_non_positive_ttl_always_refetches(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"fresh": True}}
        cache = _cache(tmp_path, http, ttl=0)
        cache.write(cache.meta_path("paper-project"), {"old": True})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {"fresh": True}

    def test_stale_cache_wins_over_network_failure(self, tmp_path) -> None:
        # stale but present: an unreachable API degrades to the cached copy
        http = OfflineHttp()
        reporter = FakeReporter()
        fs = FileStore()
        cache_dir = PathLayout(root=str(tmp_path)).cache_dir()
        fs.write_json(os.path.join(cache_dir, "meta", "forge-promotions.json"), {"promos": {"1.20.4-latest": "49.2.8"}})
        future = time.time() + DEFAULT_TTL + 1
        cache = MetadataCache(http, fs, cache_dir, now=lambda: future, reporter=reporter)
        data = cache.get_json(cache.meta_path("forge-promotions"), PROMOTIONS_URL, desc="获取 Forge 版本列表")
        assert data == {"promos": {"1.20.4-latest": "49.2.8"}}
        assert any("回退到本地缓存" in text for text in reporter.texts)

    def test_fetch_failure_without_cache_propagates(self, tmp_path) -> None:
        cache = _cache(tmp_path, OfflineHttp())
        with pytest.raises(ConnectionError):
            cache.get_json(cache.meta_path("paper-project"), "https://fill/paper")

    def test_corrupt_cache_is_a_miss(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"fresh": True}}
        cache = _cache(tmp_path, http)
        path = cache.meta_path("paper-project")
        cache.fs.ensure_dir(os.path.dirname(path))
        cache.fs.write_text(path, "{not json")
        assert cache.get_json(path, "https://fill/paper") == {"fresh": True}

    def test_meta_path_is_a_sanitized_absolute_path(self, tmp_path) -> None:
        cache = _cache(tmp_path, FakeHttp())
        path = cache.meta_path("fabric-profile", "1.20.4/../evil")
        assert path.startswith(os.path.join(str(tmp_path), "cache", "meta"))
        # platform-neutral: no separators survive inside a part, no traversal
        assert os.path.basename(path) == "1.20.4_.._evil.json"
        assert os.path.basename(os.path.dirname(path)) == "fabric-profile"

    def test_small_future_mtime_still_counts_as_fresh(self, tmp_path) -> None:
        # Windows: file time can look a few ms future-dated vs time.time()
        http = FakeHttp()  # no responses: a refetch would fail the test
        cache = _cache(tmp_path, http, now=lambda: time.time() - 0.5)
        cache.write(cache.meta_path("paper-project"), {"cached": True})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {"cached": True}
        assert http.json_calls == []

    def test_wildly_future_mtime_is_treated_as_stale(self, tmp_path) -> None:
        # a clock jump forward must not freeze the cache for good
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"fresh": True}}
        cache = _cache(tmp_path, http, now=lambda: time.time() - 3600)
        cache.write(cache.meta_path("paper-project"), {"cached": True})
        assert cache.get_json(cache.meta_path("paper-project"), "https://fill/paper") == {"fresh": True}


class TestCacheProgress:
    def test_status_line_wraps_metadata_fetch(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"versions": {}}}
        sink = FakeSink()
        cache = _cache(tmp_path, http, sink=sink)
        cache.get_json(cache.meta_path("paper-project"), "https://fill/paper", desc="获取 Paper 版本列表")
        # an indeterminate line (total None) — no silent wait on a slow API
        assert ("获取 Paper 版本列表", None) in sink.starts

    def test_no_status_line_without_desc(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"https://fill/paper": {"versions": {}}}
        sink = FakeSink()
        cache = _cache(tmp_path, http, sink=sink)
        cache.get_json(cache.meta_path("paper-project"), "https://fill/paper")
        assert sink.starts == []

    def test_version_json_download_reports_byte_progress(self, tmp_path, reporter, sink) -> None:
        http = FakeHttp()
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path), version="1.20.4")
        fs.write_json(paths.version_manifest_path(), MANIFEST)
        http.canned_archive = b'{"javaVersion": {"majorVersion": 17}}'
        mojang = Mojang(
            MetadataCache(http, fs, paths.cache_dir(), reporter=reporter, sink=sink),
            paths.version_manifest_path(),
            paths.version_jsons_dir(),
        )
        mojang.version_json("1.20.4")
        # 总量取自流式 GET 的响应头(FakeHttp 上报字节数),不再先发一次 HEAD 探测。
        assert ("下载版本元数据 1.20.4", 37) in sink.starts
        assert sink.advanced > 0


class TestProviderMetadataCaching:
    """Every third-party API adapter goes through the same cache (no repeat fetch)."""

    def test_paper_build_lookup_cached(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            "/v3/projects/paper": {"versions": {"1.20": ["1.20.4"]}},
            "/v3/projects/paper/versions/1.20.4/builds/latest": {
                "downloads": {"server:default": {"url": "https://fill-data/paper.jar"}}
            },
        }
        api = PaperAPI(_cache(tmp_path, http))
        assert api.download_url("1.20.4").endswith("paper.jar")
        calls = len(http.json_calls)
        assert api.download_url("1.20.4").endswith("paper.jar")
        assert len(http.json_calls) == calls

    def test_fabric_loader_list_cached(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {"/versions/loader/1.20.4": [{"loader": {"version": "0.19.3", "stable": True}}]}
        fabric = Fabric(_cache(tmp_path, http), "1.20.4")
        assert fabric.latest_loader_version() == "0.19.3"
        calls = len(http.json_calls)
        assert fabric.latest_loader_version() == "0.19.3"
        assert len(http.json_calls) == calls

    def test_forge_promotions_cached(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {PROMOTIONS_URL: {"promos": {"1.20.4-latest": "49.2.8"}}}
        forge = Forge(_cache(tmp_path, http))
        assert forge.latest_full_version("1.20.4") == "1.20.4-49.2.8"
        calls = len(http.json_calls)
        assert forge.latest_full_version("1.20.4") == "1.20.4-49.2.8"
        assert len(http.json_calls) == calls

    def test_refresh_flag_refetches_every_adapter(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            VERSION_MANIFEST_URL: MANIFEST,
            PROMOTIONS_URL: {"promos": {"1.20.4-latest": "49.2.8"}},
        }
        fs = FileStore()
        paths = PathLayout(root=str(tmp_path))
        cache = MetadataCache(http, fs, paths.cache_dir(), refresh=True)
        mojang = Mojang(cache, paths.version_manifest_path(), paths.version_jsons_dir())
        mojang.release_version_ids()
        Forge(cache).latest_full_version("1.20.4")
        assert len(http.json_calls) == 2
        # both are cached on disk now, but refresh keeps going to the network
        mojang.release_version_ids()
        Forge(cache).latest_full_version("1.20.4")
        assert len(http.json_calls) == 4
