"""Self-update: target resolution, guards, and staging a replacement binary.

Hermetic by construction: the install state dir is redirected into ``tmp_path``
(never the developer's real ``~/.local/state``), ``FakeHttp`` rejects unseeded
network access, and the detached swap helper is faked (its real behavior is
covered by the installer e2e job in CI).
"""

from __future__ import annotations

import os
import stat
from typing import Any

import pytest
from fakes import FakeHttp, FakeProcess, FakeReporter, FakeSink

from orzmc import UpdateCheck, check_self_update, update_self
from orzmc.infra.fs import FileStore
from orzmc.services import selfupdate
from orzmc.services.selfinstall import InstallManifest
from orzmc.services.selfupdate import (
    API_LATEST,
    DOWNLOAD_BASE,
    STAGING_NAME,
    SelfUpdater,
    applier_command,
    asset_for,
    is_newer,
    normalize_tag,
    version_key,
)

# Minimal payloads that satisfy the magic sniffing on each platform.
_ELF = b"\x7fELF" + b"\x00" * 60
_PE = b"MZ" + b"\x00" * 62


def _payload() -> bytes:
    return _PE if os.name == "nt" else _ELF


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch) -> str:
    """Redirect the manifest state dir into tmp so no test touches a real install."""
    state = str(tmp_path / "state")
    monkeypatch.setenv("XDG_STATE_HOME", state)
    monkeypatch.setenv("LOCALAPPDATA", state)
    return state


@pytest.fixture
def running_version(monkeypatch):
    """Override the version baked into the running binary (the ground truth)."""

    def _set(version: str) -> None:
        monkeypatch.setattr(selfupdate, "__version__", version)

    return _set


def _install(tmp_path, *, version: str = "v1.0.0") -> tuple[FileStore, str]:
    """Seed a fake installed binary + manifest; returns (fs, binary path)."""
    fs = FileStore()
    install_dir = str(tmp_path / "bin")
    binary = os.path.join(install_dir, "orzmc")
    fs.write_text(binary, "old-binary-content")
    InstallManifest(
        version=version,
        platform="linux-x86_64",
        install_dir=install_dir,
        binary=binary,
        source=f"{DOWNLOAD_BASE}/{version}/orzmc-linux-x86_64",
    ).write(fs)
    return fs, binary


def _new_binary(tmp_path, content: bytes | None = None) -> str:
    path = str(tmp_path / "downloads" / "orzmc-new")
    FileStore().write_text(path, (content or _payload()).decode("latin-1"))
    return path


def _updater(fs: FileStore, binary: str, http: FakeHttp, **kwargs) -> SelfUpdater:
    """An updater wired with fakes (the public API always builds a real HttpClient)."""
    asset, platform_tag = asset_for()
    return SelfUpdater(
        http=http,
        fs=fs,
        reporter=kwargs.pop("reporter", None),
        sink=kwargs.pop("sink", None),
        process=kwargs.pop("process", None) or FakeProcess(),
        binary=binary,
        asset=(asset, platform_tag),
        **kwargs,
    )


def _run_applier(process: FakeProcess, binary: str) -> None:
    """Play the detached helper's part (its real self is covered by the e2e harness).

    The staged path is deterministic, so the tests never parse the helper
    command — its shape differs per platform (POSIX passes ``staged target`` as
    argv, Windows embeds both paths inside the PowerShell script).
    """
    assert process.detached, "升级助手未被拉起"
    command = process.detached[-1]
    staging = os.path.join(os.path.dirname(binary), STAGING_NAME)
    assert any(staging in arg for arg in command), command
    assert any(binary in arg for arg in command), command
    os.replace(staging, binary)


class TestAssetFor:
    def test_maps_the_six_release_assets(self) -> None:
        assert asset_for("Darwin", "arm64") == ("orzmc-macos-arm64", "macos-arm64")
        assert asset_for("Darwin", "x86_64") == ("orzmc-macos-x86_64", "macos-x86_64")
        assert asset_for("Linux", "x86_64") == ("orzmc-linux-x86_64", "linux-x86_64")
        assert asset_for("Linux", "aarch64") == ("orzmc-linux-arm64", "linux-arm64")
        assert asset_for("Windows", "AMD64") == ("orzmc-windows-x86_64.exe", "windows-x86_64")
        assert asset_for("Windows", "ARM64") == ("orzmc-windows-arm64.exe", "windows-arm64")

    def test_rejects_unknown_system_and_arch(self) -> None:
        with pytest.raises(RuntimeError, match="暂不支持该系统"):
            asset_for("FreeBSD", "x86_64")
        with pytest.raises(RuntimeError, match="暂不支持该架构"):
            asset_for("Linux", "ppc64le")


class TestVersionComparison:
    def test_version_key_ignores_suffixes(self) -> None:
        assert version_key("v2.0.5") == (2, 0, 5)
        assert version_key("2.1.0-rc1") == (2, 1, 0)
        assert version_key("local-build") == ()

    def test_normalize_tag_adds_the_v(self) -> None:
        assert normalize_tag("2.0.5") == "v2.0.5"
        assert normalize_tag("v2.0.5") == "v2.0.5"
        with pytest.raises(RuntimeError):
            normalize_tag("   ")

    def test_is_newer(self) -> None:
        assert is_newer("v2.1.0", "v2.0.5") is True
        assert is_newer("v2.0.5", "v2.0.5") is False
        assert is_newer("v2.0.4", "v2.0.5") is False
        assert is_newer("v2.0.5", "") is True
        assert is_newer("v2.0.5", "local-build") is True


class TestApplierCommand:
    """The swap must happen in a helper that outlives us (see module docstring)."""

    @pytest.mark.skipif(os.name == "nt", reason="POSIX helper")
    def test_posix_helper_waits_for_the_pid_then_renames(self) -> None:
        cmd = applier_command("/bin/.orzmc-update.tmp", "/bin/orzmc", 4242)
        assert cmd[:2] == ["/bin/sh", "-c"]
        assert cmd[-3:] == ["4242", "/bin/.orzmc-update.tmp", "/bin/orzmc"]
        script = cmd[2]
        assert 'kill -0 "$1"' in script  # waits for our pid
        assert 'mv -f "$2" "$3"' in script  # then the atomic same-dir rename
        assert "DONE" in script and "FAILED" in script  # terminal marker for the log

    def test_windows_helper_uses_powershell_and_waits(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "name", "nt")
        cmd = applier_command(r"C:\bin\.orzmc-update.tmp", r"C:\bin\orzmc.exe", 4242)
        assert cmd[0] == "powershell"
        script = cmd[-1]
        assert "Get-Process -Id 4242" in script  # waits for our pid
        assert "Move-Item -Force" in script
        # -ErrorAction Stop is essential: without it a failed move is a
        # non-terminating error, the copy fallback never runs, and the helper
        # silently leaves the staged file behind (CI caught exactly that).
        assert "Move-Item -Force -LiteralPath $s -Destination $t -ErrorAction Stop" in script
        assert "[IO.File]::Copy($s, $t, $true)" in script
        assert "'DONE'" in script and "'FAILED'" in script
        assert r"'C:\bin\.orzmc-update.tmp'" in script

    def test_windows_helper_escapes_single_quotes(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "name", "nt")
        cmd = applier_command("/tmp/it's/.orzmc-update.tmp", "/tmp/it's/orzmc", 1)
        assert "'/tmp/it''s/.orzmc-update.tmp'" in cmd[-1]


class TestCheck:
    def test_explicit_version_never_calls_the_api(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        http = FakeHttp()
        updater = SelfUpdater(http=http, fs=fs, binary=binary)
        check = updater.check("2.1.0")
        assert check.latest == "v2.1.0"
        assert check.current == f"v{selfupdate.__version__}"
        assert check.url == f"{DOWNLOAD_BASE}/v2.1.0/{check.asset}"
        assert check.available is True
        assert http.json_calls == []

    def test_latest_release_from_github(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"tag_name": "v2.1.0"}}
        check = SelfUpdater(http=http, fs=fs, binary=binary).check()
        assert (check.latest, check.available) == ("v2.1.0", True)
        assert http.json_calls == [API_LATEST]

    def test_api_failure_suggests_an_explicit_version(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        with pytest.raises(RuntimeError, match="--version"):
            SelfUpdater(http=FakeHttp(), fs=fs, binary=binary).check()

    def test_api_response_without_tag_is_an_error(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"name": "no tag here"}}
        with pytest.raises(RuntimeError, match="没有版本标签"):
            SelfUpdater(http=http, fs=fs, binary=binary).check()

    def test_current_version_is_the_running_binary(self, tmp_path, running_version) -> None:
        # the install record may be synthetic/stale; the baked-in version cannot lie
        fs, binary = _install(tmp_path, version="local-build")
        running_version("3.2.1")
        assert SelfUpdater(http=FakeHttp(), fs=fs, binary=binary).current_version() == "v3.2.1"

    def test_public_api_check_with_explicit_version(self, tmp_path) -> None:
        _fs, binary = _install(tmp_path)
        check = check_self_update(binary, version="v9.9.9")
        assert isinstance(check, UpdateCheck)
        assert check.latest == "v9.9.9"


class TestUpdateFromLocalFile:
    def test_stages_then_hands_off_to_the_helper(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        process = FakeProcess()
        reporter = FakeReporter()
        result = update_self(
            binary,
            file=_new_binary(tmp_path),
            version="v2.0.0",
            yes=True,
            reporter=reporter,
            sink=FakeSink(),
            process=process,
        )
        assert result is not None and result.applied is True
        # the running binary is never swapped by this process …
        assert fs.read_text(binary) == "old-binary-content"
        assert os.path.exists(os.path.join(os.path.dirname(binary), STAGING_NAME))
        # … the helper does it after we exit
        _run_applier(process, binary)
        assert fs.read_text(binary) == _payload().decode("latin-1")
        assert not os.path.exists(os.path.join(os.path.dirname(binary), STAGING_NAME))
        assert any("本命令退出后生效" in text for text in reporter.texts)
        found = InstallManifest.find(fs, binary=binary)
        assert found is not None and found[1].version == "v2.0.0"
        assert found[1].source is None  # local installs carry no download source

    @pytest.mark.skipif(os.name == "nt", reason="POSIX exec-bit behavior")
    def test_staged_binary_is_executable(self, tmp_path) -> None:
        _fs, binary = _install(tmp_path)
        process = FakeProcess()
        update_self(binary, file=_new_binary(tmp_path), version="v2.0.0", yes=True, process=process)
        _run_applier(process, binary)
        assert os.stat(binary).st_mode & stat.S_IXUSR

    def test_non_executable_payload_is_rejected(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        html = str(tmp_path / "downloads" / "index.html")
        fs.write_text(html, "<!DOCTYPE html>not a binary")
        process = FakeProcess()
        with pytest.raises(RuntimeError, match="不是可执行文件"):
            update_self(binary, file=html, version="v2.0.0", yes=True, process=process)
        assert fs.read_text(binary) == "old-binary-content"  # untouched
        assert process.detached == []  # nothing was handed off
        assert not fs.is_file(os.path.join(os.path.dirname(binary), STAGING_NAME))

    def test_local_file_requires_a_version(self, tmp_path) -> None:
        _fs, binary = _install(tmp_path)
        with pytest.raises(RuntimeError, match="必须同时指定版本"):
            update_self(binary, file=_new_binary(tmp_path), yes=True)

    def test_missing_local_file_is_an_error(self, tmp_path) -> None:
        _fs, binary = _install(tmp_path)
        with pytest.raises(RuntimeError, match="本地文件不存在"):
            update_self(binary, file=str(tmp_path / "nope"), version="v2.0.0", yes=True)

    def test_declined_confirmation_changes_nothing(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        process = FakeProcess()
        result = update_self(
            binary,
            file=_new_binary(tmp_path),
            version="v2.0.0",
            confirm=lambda _desc: False,
            process=process,
        )
        assert result is None
        assert process.detached == []
        assert fs.read_text(binary) == "old-binary-content"

    def test_missing_manifest_still_upgrades_and_warns(self, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "old")
        reporter = FakeReporter()
        result = update_self(
            binary,
            file=_new_binary(tmp_path),
            version="v2.0.0",
            yes=True,
            reporter=reporter,
            process=FakeProcess(),
        )
        assert result is not None and result.applied is True
        assert any("未找到安装记录" in text for text in reporter.texts)

    def test_helper_launch_failure_keeps_the_old_binary(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)

        class _BrokenProcess(FakeProcess):
            def run_detached(self, args: list[str], cwd: str | None = None, log_path: str | None = None) -> Any:
                raise OSError("no helper today")

        with pytest.raises(RuntimeError, match="无法启动升级助手"):
            update_self(
                binary,
                file=_new_binary(tmp_path),
                version="v2.0.0",
                yes=True,
                process=_BrokenProcess(),
            )
        assert fs.read_text(binary) == "old-binary-content"
        assert not fs.is_file(os.path.join(os.path.dirname(binary), STAGING_NAME))


class TestUpdateFromNetwork:
    def test_downloads_and_hands_off_the_matching_asset(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        asset, _tag = asset_for()
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"tag_name": "v2.1.0"}}
        http.canned = {asset: _payload()}
        process, reporter = FakeProcess(), FakeReporter()
        result = _updater(fs, binary, http, reporter=reporter, sink=FakeSink(), process=process).update(yes=True)
        assert result is not None and result.applied is True
        assert [url for _, url in http.requests] == [f"{DOWNLOAD_BASE}/v2.1.0/{asset}"]
        _run_applier(process, binary)
        assert fs.read_text(binary) == _payload().decode("latin-1")
        found = InstallManifest.find(fs, binary=binary)
        assert found is not None
        assert found[1].version == "v2.1.0"
        assert found[1].source == f"{DOWNLOAD_BASE}/v2.1.0/{asset}"

    def test_already_latest_skips_the_download(self, tmp_path, running_version) -> None:
        fs, binary = _install(tmp_path, version="v2.1.0")
        running_version("2.1.0")
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"tag_name": "v2.1.0"}}
        reporter = FakeReporter()
        result = _updater(fs, binary, http, reporter=reporter).update(yes=True)
        assert result is not None and result.applied is False
        assert http.requests == []  # no asset download attempted
        assert any("已是最新版本" in text for text in reporter.texts)

    def test_local_version_newer_than_latest_is_kept(self, tmp_path, running_version) -> None:
        fs, binary = _install(tmp_path, version="v9.0.0")
        running_version("9.0.0")
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"tag_name": "v2.1.0"}}
        reporter = FakeReporter()
        result = _updater(fs, binary, http, reporter=reporter).update(yes=True)
        assert result is not None and result.applied is False
        assert http.requests == []
        assert any("已跳过升级" in text for text in reporter.texts)

    def test_explicit_version_reinstalls_the_same_version(self, tmp_path, running_version) -> None:
        fs, binary = _install(tmp_path, version="v1.0.0")
        running_version("1.0.0")
        asset, _tag = asset_for()
        http = FakeHttp()
        http.canned = {asset: _payload()}
        result = _updater(fs, binary, http).update("v1.0.0", yes=True)
        assert result is not None and result.applied is True
        assert http.json_calls == []  # explicit version bypasses the API

    def test_download_failure_leaves_the_binary_intact(self, tmp_path) -> None:
        fs, binary = _install(tmp_path)
        http = FakeHttp()
        http.json_responses = {API_LATEST: {"tag_name": "v2.1.0"}}
        process = FakeProcess()
        with pytest.raises(RuntimeError, match="下载"):
            _updater(fs, binary, http, process=process).update(yes=True)
        assert process.detached == []
        assert fs.read_text(binary) == "old-binary-content"
        assert not fs.is_file(os.path.join(os.path.dirname(binary), STAGING_NAME))


class TestGuards:
    def test_pip_managed_install_is_refused(self, tmp_path) -> None:
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc")
        fs.write_text(binary, "x")
        fs.ensure_dir(os.path.join(install_dir, "orzmc_app-2.0.5.dist-info"))
        with pytest.raises(RuntimeError, match="pip install -U orzmc-app"):
            update_self(binary, file=_new_binary(tmp_path), version="v2.0.0", yes=True)
        assert fs.read_text(binary) == "x"

    def test_venv_binary_is_refused_without_force(self, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / ".venv" / "bin" / "orzmc")
        fs.write_text(binary, "x")
        with pytest.raises(RuntimeError, match="已拒绝自升级"):
            update_self(binary, file=_new_binary(tmp_path), version="v2.0.0", yes=True)

    def test_force_bypasses_only_the_dev_guard(self, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / ".venv" / "bin" / "orzmc")
        fs.write_text(binary, "x")
        process = FakeProcess()
        result = update_self(
            binary, file=_new_binary(tmp_path), version="v2.0.0", yes=True, force=True, process=process
        )
        assert result is not None and result.applied is True
        _run_applier(process, binary)
        assert fs.read_text(binary) == _payload().decode("latin-1")
