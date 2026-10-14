"""Cross-face GUI app installer (offline; fake HTTP/process, sandboxed HOME)."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

import pytest
from fakes import FakeHttp, FakeProcess, FakeReporter

from orzmc.services.app import AppInstaller

API_LATEST = "https://api.github.com/repos/OrzMC/OrzMCApp/releases/latest"
macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="GUI app 仅 macOS")


class _ExtractingProcess(FakeProcess):
    """Fake ``ditto`` that materializes ``OrzMC.app`` inside the extract dir."""

    def run_stream(self, cmd, on_line=None, cwd=None):
        self.calls.append(list(cmd))
        if cmd[0].endswith("ditto"):
            contents = os.path.join(cmd[-1], "OrzMC.app", "Contents")
            os.makedirs(contents, exist_ok=True)
            with open(os.path.join(contents, "Info.plist"), "wb") as handle:
                plistlib.dump({"CFBundleShortVersionString": "0.1.2"}, handle)
        return 0


def _release() -> dict[str, object]:
    return {
        "tag_name": "0.1.2",
        "assets": [
            {"browser_download_url": "https://x/OrzMC_0.1.2_1_20250101.zip"},
            {"browser_download_url": "https://x/OrzMC_0.1.2_9_20260101.zip"},
            {"browser_download_url": "https://x/OrzMC_0.1.2.dmg"},
        ],
    }


def test_status_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows 的 expanduser("~")
    installer = AppInstaller(http=FakeHttp(), reporter=FakeReporter())
    state = installer.status()
    assert state.installed is False
    assert state.path == str(tmp_path / "Applications" / "OrzMC.app")
    assert state.version is None


def test_resolve_picks_newest_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    http = FakeHttp()
    http.json_responses[API_LATEST] = _release()
    tag, url = AppInstaller(http=http, reporter=FakeReporter()).resolve()
    assert tag == "0.1.2"
    assert url.endswith("20260101.zip")


@macos_only
def test_install_extracts_and_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    http = FakeHttp()
    http.json_responses[API_LATEST] = _release()
    http.canned["20260101.zip"] = b"zip-bytes"
    installer = AppInstaller(http=http, process=_ExtractingProcess(), reporter=FakeReporter())

    path = installer.install()
    assert path == str(tmp_path / "Applications" / "OrzMC.app")
    assert installer.status().version == "0.1.2"


@macos_only
def test_uninstall_removes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    installer = AppInstaller(http=FakeHttp(), reporter=FakeReporter())
    assert installer.uninstall() is False
    os.makedirs(installer.installed_path, exist_ok=True)
    assert installer.uninstall() is True
    assert not os.path.exists(installer.installed_path)
