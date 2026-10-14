"""Hangar plugin service (offline; fake HTTP, real tmp dirs)."""

from __future__ import annotations

from pathlib import Path

from fakes import FakeHttp, FakeReporter, FakeSink

from orzmc.services.plugins import DEFAULT_PLUGINS, PluginService

BASE = "https://hangar.papermc.io/api/v1"


def _service(http: FakeHttp) -> PluginService:
    return PluginService(http=http, reporter=FakeReporter(), sink=FakeSink())


def test_default_plugin_set_is_populated() -> None:
    assert "LuckPerms" in DEFAULT_PLUGINS
    assert len(DEFAULT_PLUGINS) >= 10


def test_search_returns_projects() -> None:
    http = FakeHttp()
    http.json_responses[BASE + "/projects"] = {
        "result": [{"name": "LuckPerms", "description": "perms", "stats": {"downloads": 5, "stars": 2}}]
    }
    results = _service(http).search("luckperms", 3)
    assert [p["name"] for p in results] == ["LuckPerms"]
    assert http.json_calls == [BASE + "/projects"]


def test_list_all_reports_not_found() -> None:
    http = FakeHttp()
    http.json_responses[BASE + "/projects"] = {"result": [{"name": "LuckPerms", "namespace": {"slug": "LuckPerms"}}]}
    found, not_found = _service(http).list_all()
    assert [p["name"] for p in found] == ["LuckPerms"]
    assert "OrzMC" in not_found


def test_download_writes_latest_release(tmp_path: Path) -> None:
    http = FakeHttp()
    http.json_responses[BASE + "/projects"] = {"result": [{"name": "LuckPerms", "namespace": {"slug": "LuckPerms"}}]}
    http.text_responses["/projects/LuckPerms/latest"] = "5.4.0\n"
    http.json_responses[BASE + "/projects/LuckPerms/versions/5.4.0"] = {
        "downloads": {
            "PAPER": {
                "fileInfo": {"name": "LuckPerms-5.4.0.jar"},
                "downloadUrl": "https://cdn.example/lp.jar",
            }
        }
    }
    http.canned["https://cdn.example/lp.jar"] = b"jar-bytes"

    files = _service(http).download(str(tmp_path))
    assert files == ["LuckPerms-5.4.0.jar"]
    assert (tmp_path / "LuckPerms-5.4.0.jar").read_bytes() == b"jar-bytes"
