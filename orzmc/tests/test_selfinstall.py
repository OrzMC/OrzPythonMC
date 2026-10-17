"""SelfUninstaller + InstallManifest: manifest round-trip, uninstall side effects.

Uses real tmp dirs + FileStore; the state dir is redirected via XDG_STATE_HOME /
LOCALAPPDATA so tests are hermetic on every platform.
"""

from __future__ import annotations

import os
import sys

import pytest
from fakes import FakeReporter

from orzmc import InstallManifest as PublicInstallManifest
from orzmc import uninstall_self
from orzmc.infra.fs import FileStore
from orzmc.services.selfinstall import InstallManifest, SelfUninstaller, default_state_dir, running_binary

skip_windows = pytest.mark.skipif(os.name == "nt", reason="Unix-only behavior")
skip_unix = pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")


def _has(reporter: FakeReporter, fragment: str) -> bool:
    """True when any reported message contains ``fragment`` (many carry a path suffix)."""
    return any(fragment in text for text in reporter.texts)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch) -> str:
    """Redirect the manifest state dir into tmp; works on Unix and Windows.

    autouse: EVERY test in this module must be hermetic. A test that calls
    ``SelfUninstaller.uninstall`` without redirecting ``XDG_STATE_HOME`` falls
    through to the real ``~/.local/state/orzmc/install.conf`` and would
    uninstall the developer's actual install (deleted a real binary once).
    """
    state = str(tmp_path / "state")
    monkeypatch.setenv("XDG_STATE_HOME", state)
    monkeypatch.setenv("LOCALAPPDATA", state)
    return state


def _seed(
    fs: FileStore,
    state_dir: str,
    *,
    binary: str,
    install_dir: str | None = None,
    path_file: str | None = None,
    path_line: str | None = None,
    root_dir: str | None = None,
) -> InstallManifest:
    manifest = InstallManifest(
        version="v2.0.1",
        platform="macos-arm64",
        install_dir=install_dir or os.path.dirname(binary),
        binary=binary,
        source="https://example.com/orzmc-macos-arm64",
        path_file=path_file,
        path_line=path_line,
        root_dir=root_dir or os.path.join(state_dir, "mcroot"),
    )
    manifest.write(fs)
    return manifest


class TestDefaultStateDir:
    @skip_windows
    def test_uses_xdg_state_home(self, tmp_path, monkeypatch) -> None:
        state = str(tmp_path / "xdg-state")
        monkeypatch.setenv("XDG_STATE_HOME", state)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert default_state_dir() == os.path.join(state, "orzmc")

    @skip_windows
    def test_fallback_home_state(self, monkeypatch) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        expected = os.path.join(os.path.expanduser("~"), ".local", "state", "orzmc")
        assert default_state_dir() == expected

    @skip_unix
    def test_uses_localappdata(self, tmp_path, monkeypatch) -> None:
        state = str(tmp_path / "local-app-data")
        monkeypatch.setenv("LOCALAPPDATA", state)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert default_state_dir() == os.path.join(state, "orzmc")


class TestPublicApi:
    def test_manifest_exported(self) -> None:
        assert PublicInstallManifest is InstallManifest

    def test_uninstall_self(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        _seed(fs, state_dir, binary=binary)

        result = uninstall_self(binary, yes=True)

        assert result is True
        assert not fs.exists(binary)
        assert not fs.exists(os.path.join(state_dir, "orzmc", "install.conf"))


class TestInstallManifest:
    def test_roundtrip(self, tmp_path) -> None:
        fs = FileStore()
        path = str(tmp_path / "install.conf")
        manifest = InstallManifest(
            version="v2.0.1",
            platform="macos-arm64",
            install_dir="/x/bin",
            binary="/x/bin/orzmc",
            source="https://example.com/orzmc-macos-arm64",
            path_file="/home/u/.zshrc",
            path_line='export PATH="/x/bin:$PATH"',
            root_dir="/home/u/minecraft",
        )
        manifest.write(fs, path)
        assert InstallManifest.read(fs, path) == manifest
        text = fs.read_text(path)
        assert "path_file=/home/u/.zshrc" in text
        assert 'path_line=export PATH="/x/bin:$PATH"' in text
        assert text.endswith("root_dir=/home/u/minecraft\n")

    def test_omits_none_optionals(self, tmp_path) -> None:
        fs = FileStore()
        path = str(tmp_path / "install.conf")
        InstallManifest(binary="/x/orzmc", install_dir="/x").write(fs, path)
        text = fs.read_text(path)
        assert "source=" not in text
        assert "path_file=" not in text
        assert "path_line=" not in text

    def test_from_lines_tolerates_unknown_and_bad_schema(self) -> None:
        manifest = InstallManifest.from_lines(["tool=orzmc", "schema=abc", "unknown=1", "binary=/x/orzmc"])
        assert manifest.schema == 1
        assert manifest.binary == "/x/orzmc"
        assert manifest.source is None
        assert manifest.path_file is None

    def test_values_keep_equals_and_spaces(self) -> None:
        manifest = InstallManifest.from_lines(['path_line=export PATH="/a b/bin:$PATH"'])
        assert manifest.path_line == 'export PATH="/a b/bin:$PATH"'

    def test_read_tolerates_bom(self, tmp_path) -> None:
        fs = FileStore()
        path = str(tmp_path / "install.conf")
        fs.write_text(path, "﻿tool=orzmc\nbinary=/x/orzmc\n", encoding="utf-8")
        manifest = InstallManifest.read(fs, path)
        assert manifest.tool == "orzmc"
        assert manifest.binary == "/x/orzmc"

    def test_find_prefers_state_dir(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        _seed(fs, state_dir, binary=binary)
        fs.write_text(os.path.join(os.path.dirname(binary), ".orzmc-manifest"), "binary=other\n")
        found = InstallManifest.find(fs, binary=binary)
        assert found is not None
        path, manifest = found
        assert path.endswith("install.conf")
        assert manifest.binary == binary

    def test_find_falls_back_next_to_binary(self, tmp_path, monkeypatch) -> None:
        state = str(tmp_path / "state")
        monkeypatch.setenv("XDG_STATE_HOME", state)
        monkeypatch.setenv("LOCALAPPDATA", state)
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        fs.write_text(
            os.path.join(os.path.dirname(binary), ".orzmc-manifest"), "binary=/x/bin/orzmc\ninstall_dir=/x/bin\n"
        )
        found = InstallManifest.find(fs, binary=binary)
        assert found is not None
        assert found[0].endswith(".orzmc-manifest")
        assert found[1].binary == "/x/bin/orzmc"

    def test_find_returns_none_when_missing(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        assert InstallManifest.find(fs, binary=binary) is None


class TestSelfUninstaller:
    def test_uninstall_removes_binary_and_exact_rc_line(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc")
        fs.write_text(binary, "#!/bin/sh\necho orzmc\n")
        rc = str(tmp_path / ".zshrc")
        path_line = f'export PATH="{install_dir}:$PATH"'
        fs.write_text(rc, path_line + "\nalias foo=bar\n")
        _seed(fs, state_dir, binary=binary, install_dir=install_dir, path_file=rc, path_line=path_line)
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert not fs.exists(binary)
        assert not fs.exists(os.path.join(state_dir, "orzmc", "install.conf"))
        rc_text = fs.read_text(rc)
        assert path_line not in rc_text
        assert "alias foo=bar" in rc_text
        assert not os.path.isdir(install_dir)
        assert "已卸载 orzmc" in reporter.texts

    @skip_windows
    def test_restore_path_missing_rc_is_skipped(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        missing_rc = str(tmp_path / "nope" / ".zshrc")
        _seed(fs, state_dir, binary=binary, path_file=missing_rc, path_line='export PATH="/x:$PATH"')
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert _has(reporter, "PATH 配置文件不存在,跳过还原")

    @skip_windows
    def test_restore_path_handles_missing_line(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        rc = str(tmp_path / ".zshrc")
        fs.write_text(rc, "alias foo=bar\n")
        _seed(fs, state_dir, binary=binary, path_file=rc, path_line='export PATH="/x:$PATH"')
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert _has(reporter, "未在 rc 文件中找到记录的 PATH 行")
        assert "alias foo=bar" in fs.read_text(rc)

    def test_uninstall_keeps_nonempty_install_dir(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc")
        fs.write_text(binary, "x")
        fs.write_text(os.path.join(install_dir, "other-tool"), "y")
        _seed(fs, state_dir, binary=binary, install_dir=install_dir)

        result = SelfUninstaller(fs=fs).uninstall(binary, yes=True)

        assert result is True
        assert not fs.exists(binary)
        assert fs.is_file(os.path.join(install_dir, "other-tool"))

    def test_safety_guard_refuses_venv(self, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / ".venv" / "bin" / "orzmc")
        fs.write_text(binary, "x")
        with pytest.raises(RuntimeError):
            SelfUninstaller(fs=fs).uninstall(binary, yes=True)
        # --force bypasses the guard
        result = SelfUninstaller(fs=fs).uninstall(binary, yes=True, force=True)
        assert result is True
        assert not fs.exists(binary)

    def test_safety_guard_refuses_site_packages(self, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "site-packages" / "bin" / "orzmc")
        fs.write_text(binary, "x")
        with pytest.raises(RuntimeError):
            SelfUninstaller(fs=fs).uninstall(binary, yes=True)

    def test_missing_manifest_still_removes_binary(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert not fs.exists(binary)
        assert _has(reporter, "未找到安装记录,已尽力清理")

    def test_pip_managed_binary_is_kept(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc")
        fs.write_text(binary, "x")
        fs.ensure_dir(os.path.join(install_dir, "orzmc_app-2.0.1.dist-info"))
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is False
        assert fs.exists(binary)
        assert _has(reporter, "请用 pip uninstall orzmc-app 卸载")

    def test_root_kept_by_default(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        root = str(tmp_path / "mcroot")
        fs.ensure_dir(root)
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        _seed(fs, state_dir, binary=binary, root_dir=root)
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert os.path.isdir(root)
        assert _has(reporter, "游戏数据保留")

    def test_root_removed_with_remove_root(self, state_dir, tmp_path) -> None:
        fs = FileStore()
        root = str(tmp_path / "mcroot")
        fs.ensure_dir(root)
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        _seed(fs, state_dir, binary=binary, root_dir=root)
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True, remove_root=True)

        assert result is True
        assert not os.path.isdir(root)
        assert _has(reporter, "已删除游戏数据")

    @pytest.mark.parametrize(
        "confirm_result, expected",
        [(True, False), (False, True), (None, True)],
    )
    def test_root_follows_confirm(self, state_dir, tmp_path, confirm_result, expected) -> None:
        fs = FileStore()
        root = str(tmp_path / "mcroot")
        fs.ensure_dir(root)
        binary = str(tmp_path / "bin" / "orzmc")
        fs.write_text(binary, "x")
        _seed(fs, state_dir, binary=binary, root_dir=root)

        confirm_fn = None if confirm_result is None else lambda _: confirm_result
        SelfUninstaller(fs=fs).uninstall(binary, remove_root=False, yes=False, confirm=confirm_fn)

        assert os.path.isdir(root) is expected


class TestWindowsPathRestore:
    """Windows PATH restore (_restore_windows_path): runs only on Windows CI.

    The library imports ``winreg`` lazily, so we swap a fake module into
    ``sys.modules`` and assert the recorded ``path_line`` token is removed from
    the User PATH without touching a real registry.
    """

    class _FakeWinReg:
        def __init__(self) -> None:
            self.path = ""
            self.opened: list[str] = []
            self.HKEY_CURRENT_USER = "HKCU"
            self.KEY_QUERY_VALUE = 1
            self.KEY_SET_VALUE = 2
            self.REG_EXPAND_SZ = 3

        def OpenKey(self, key, subkey, reserved=0, access=0):
            self.opened.append(subkey)
            return self

        def QueryValueEx(self, key, name):
            return (self.path, self.REG_EXPAND_SZ)

        def SetValueEx(self, key, name, reserved, typ, value):
            self.path = value

        def CloseKey(self, key) -> None:
            pass

    @skip_unix
    def test_removes_recorded_token(self, state_dir, tmp_path, monkeypatch) -> None:
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc.exe")
        fs.write_text(binary, "x")
        fake = self._FakeWinReg()
        fake.path = "C:\\Users\\u\\bin;" + install_dir
        monkeypatch.setitem(sys.modules, "winreg", fake)
        _seed(fs, state_dir, binary=binary, path_line=install_dir)
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert not fs.exists(binary)
        assert fake.path.split(";") == ["C:\\Users\\u\\bin"]
        assert fake.opened == ["Environment"]

    @skip_unix
    def test_skips_when_token_missing(self, state_dir, tmp_path, monkeypatch) -> None:
        fs = FileStore()
        binary = str(tmp_path / "bin" / "orzmc.exe")
        fs.write_text(binary, "x")
        fake = self._FakeWinReg()
        fake.path = "C:\\Users\\u\\bin"
        monkeypatch.setitem(sys.modules, "winreg", fake)
        _seed(fs, state_dir, binary=binary, path_line=str(tmp_path / "other"))
        reporter = FakeReporter()

        result = SelfUninstaller(fs=fs, reporter=reporter).uninstall(binary, yes=True)

        assert result is True
        assert fake.path == "C:\\Users\\u\\bin"  # 未改动
        assert not _has(reporter, "无法修改 Windows 用户 PATH")

    def test_rc_file_restore_has_priority_on_windows(self, state_dir, tmp_path, monkeypatch) -> None:
        """rc 文件式安装(path_file+path_line)在 Windows 上也还原 rc,不碰注册表。

        回归:os.name=="nt" 曾让 _restore_path 无条件走注册表,rc 行未删
        (Windows CI 上 test_uninstall_removes_binary_and_exact_rc_line 失败)。
        本测试在 macOS/Linux 用 monkeypatch 模拟 nt,验证路由与注册表隔离。
        """
        fs = FileStore()
        install_dir = str(tmp_path / "bin")
        binary = os.path.join(install_dir, "orzmc.exe")
        fs.write_text(binary, "x")
        rc = str(tmp_path / ".zshrc")
        path_line = f'export PATH="{install_dir}:$PATH"'
        fs.write_text(rc, path_line + "\nalias foo=bar\n")
        _seed(fs, state_dir, binary=binary, install_dir=install_dir, path_file=rc, path_line=path_line)
        fake = self._FakeWinReg()
        fake.path = "C:\\Users\\u\\bin"
        monkeypatch.setitem(sys.modules, "winreg", fake)
        monkeypatch.setattr(os, "name", "nt")

        result = SelfUninstaller(fs=fs).uninstall(binary, yes=True)

        assert result is True
        rc_text = fs.read_text(rc)
        assert path_line not in rc_text
        assert "alias foo=bar" in rc_text
        assert fake.opened == []  # 注册表从未被打开
        assert fake.path == "C:\\Users\\u\\bin"


class TestRunningBinary:
    """``running_binary`` resolves the *installed* binary, never a CWD decoy.

    Regression: ``orzmc update``/``uninstall`` used
    ``Path(sys.argv[0]).resolve()``. A bare ``orzmc`` (invoked through ``PATH``)
    is just the word ``orzmc``, so it resolved against the CWD — updating or
    creating ``./orzmc`` and leaving the installed binary (and the user) on the
    old version.
    """

    def test_bare_name_resolves_via_path_not_cwd(self, tmp_path, monkeypatch) -> None:
        cwd = tmp_path / "repo"
        cwd.mkdir()
        (cwd / "orzmc").write_text("decoy")
        monkeypatch.chdir(cwd)
        installed = tmp_path / "bin" / "orzmc"
        installed.parent.mkdir()
        installed.write_text("real")
        resolved = running_binary("orzmc", which=lambda _name: str(installed))
        assert resolved == os.path.realpath(str(installed))
        assert resolved != os.path.realpath(str(cwd / "orzmc"))

    def test_explicit_path_ignores_path_lookup(self, tmp_path) -> None:
        explicit = tmp_path / "somewhere" / "orzmc"
        explicit.parent.mkdir()
        explicit.write_text("x")
        assert running_binary(str(explicit), which=lambda _name: "/decoy/orzmc") == os.path.realpath(str(explicit))

    def test_falls_back_to_executable_when_absent_from_path(self, tmp_path) -> None:
        fallback = tmp_path / "python3"
        fallback.write_text("x")
        resolved = running_binary("orzmc", which=lambda _name: None, executable=str(fallback))
        assert resolved == os.path.realpath(str(fallback))

    def test_reads_real_argv0_and_path_by_default(self, tmp_path, monkeypatch) -> None:
        # On Windows `shutil.which` only matches PATHEXT names, so the fixture is `orzmc.exe`.
        exe = "orzmc.exe" if os.name == "nt" else "orzmc"
        installed = tmp_path / "bin" / exe
        installed.parent.mkdir()
        installed.write_text("x")
        installed.chmod(0o755)  # shutil.which requires an executable bit (POSIX)
        monkeypatch.setattr(sys, "argv", [exe, "update"])
        monkeypatch.setenv("PATH", str(installed.parent))
        assert running_binary() == os.path.realpath(str(installed))

    def test_frozen_build_uses_its_own_executable(self, tmp_path, monkeypatch) -> None:
        # PyInstaller's sys.executable is the installed binary; a CWD namesake
        # (which `shutil.which` would return on Windows) must not win.
        cwd = tmp_path / "repo"
        cwd.mkdir()
        decoy = cwd / "orzmc"
        decoy.write_text("decoy")
        monkeypatch.chdir(cwd)
        real = tmp_path / "bin" / "orzmc"
        real.parent.mkdir()
        real.write_text("real")
        resolved = running_binary("orzmc", executable=str(real), frozen=True, which=lambda _n: str(decoy))
        assert resolved == os.path.realpath(str(real))

    def test_cwd_lookup_is_discarded_when_not_frozen(self, tmp_path, monkeypatch) -> None:
        cwd = tmp_path / "repo"
        cwd.mkdir()
        decoy = cwd / "orzmc"
        decoy.write_text("decoy")
        monkeypatch.chdir(cwd)
        real = tmp_path / "python3"
        real.write_text("x")
        resolved = running_binary("orzmc", executable=str(real), frozen=False, which=lambda _n: str(decoy))
        assert resolved == os.path.realpath(str(real))
