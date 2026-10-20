"""运行时分发(M3)单测:资产解析 / SHA256SUMS 校验 / 原子落盘 / 转发语义。

规范:`docs/spec/install-update.md` §10.1。全程离线(FakeHttp),不碰真实网络。
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import zipfile

import pytest
from fakes import FakeHttp

from orzmc.infra.fs import FileStore
from orzmc.services.runtime import (
    RUNTIME_ENV,
    RuntimeChannel,
    RuntimeChannelError,
    looks_like_executable,
    parse_sha256sums,
    payload_asset,
    resolve_runtime,
    runtime_mode,
)

SAFE_ELF = b"\x7fELF" + b"\x00" * 60
HTML_ERROR = b"<!DOCTYPE html><html>404</html>"


def _sums(name: str, payload: bytes) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {name}\n"


def _channel(tmp_path, http: FakeHttp, *, asset: str = "orzmc-linux-x86_64", windows: bool = False) -> RuntimeChannel:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        # Windows 形态要 USERPROFILE / LOCALAPPDATA(显式 environ 是权威,不做宿主兜底)。
        "USERPROFILE": str(tmp_path / "home"),
        "LOCALAPPDATA": str(tmp_path / "state" / "local"),
    }
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return RuntimeChannel(
        http=http,
        fs=FileStore(),
        environ=env,
        system="windows" if windows else "linux",
        machine="arm64" if windows else "x86_64",
        is_windows=windows,
    )


# ── 纯函数 ────────────────────────────────────────────────────────────────────


class TestRuntimeMode:
    def test_explicit_python(self) -> None:
        assert runtime_mode({}) == "python"
        assert runtime_mode({RUNTIME_ENV: "python"}) == "python"

    def test_explicit_swift(self) -> None:
        assert runtime_mode({RUNTIME_ENV: "swift"}) == "swift"
        assert runtime_mode({RUNTIME_ENV: " SWIFT "}) == "swift"

    def test_unknown_value_keeps_python(self) -> None:
        assert runtime_mode({RUNTIME_ENV: "whatever"}) == "python"


class TestResolveRuntime:
    """规范 §10.1「默认判定」: 新装给 swift, 存量安装保持 python."""

    def _env(self, tmp_path) -> dict[str, str]:
        # 注入的 env 是 **unix 形状**(HOME/XDG_STATE_HOME),所以显式声明 is_windows=False ——
        # 否则在 Windows runner 上会按 LOCALAPPDATA/USERPROFILE 解析,读不到我们写的记录
        # (判定逻辑本身与平台无关,这里测的是判定逻辑)。
        return {"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state")}

    def _resolve(self, tmp_path, **extra: str) -> tuple[str, str]:
        return resolve_runtime({**self._env(tmp_path), **extra}, is_windows=False)

    def _record(self, tmp_path, body: str) -> None:
        state = tmp_path / "state" / "orzmc"
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.conf").write_text(body, encoding="utf-8")

    def test_fresh_install_defaults_to_swift(self, tmp_path) -> None:
        assert self._resolve(tmp_path) == ("swift", "fresh-install")

    def test_legacy_installer_user_stays_python(self, tmp_path) -> None:
        self._record(tmp_path, "tool=orzmc\nversion=2.16.0\nbinary=/x/orzmc\nsource=github:OrzMC/OrzPythonMC\n")
        assert self._resolve(tmp_path) == ("python", "record:legacy")

    def test_native_swift_install_uses_swift(self, tmp_path) -> None:
        self._record(tmp_path, "tool=orzmc\nversion=0.18.0\nbinary=/x/orzmc\nsource=github:OrzMC/OrzMCKit\n")
        assert self._resolve(tmp_path) == ("swift", "record:native")

    def test_wrapper_channel_uses_swift(self, tmp_path) -> None:
        self._record(tmp_path, "tool=orzmc\nversion=0.18.0\nbinary=/x/orzmc\nchannel=wrapper\n")
        assert self._resolve(tmp_path) == ("swift", "record:wrapper")

    def test_env_always_wins(self, tmp_path) -> None:
        self._record(tmp_path, "tool=orzmc\nversion=0.18.0\nbinary=/x/orzmc\nchannel=wrapper\n")
        assert self._resolve(tmp_path, **{RUNTIME_ENV: "python"}) == ("python", "env:python")
        assert resolve_runtime({RUNTIME_ENV: "swift"}, is_windows=False) == ("swift", "env:swift")


class TestPayloadAsset:
    def test_posix(self) -> None:
        assert payload_asset("Darwin", "arm64") == ("orzmc-macos-arm64", "macos-arm64")
        assert payload_asset("Linux", "x86_64")[0] == "orzmc-linux-x86_64"

    def test_windows_is_zip(self) -> None:
        # Windows 资产是 zip(exe + 运行时 DLL),规范 §10.1。
        assert payload_asset("Windows", "AMD64") == ("orzmc-windows-x86_64.zip", "windows-x86_64")

    def test_unsupported_platform_has_error_code(self) -> None:
        with pytest.raises(RuntimeChannelError) as excinfo:
            payload_asset("Plan9", "mips")
        assert excinfo.value.code == "E_UNSUPPORTED"


class TestSumsAndMagic:
    def test_parse_both_separators(self) -> None:
        text = f"{'a' * 64}  orzmc-linux-x86_64\n{'b' * 64} *orzmc-windows-arm64.zip\n"
        assert parse_sha256sums(text) == {
            "orzmc-linux-x86_64": "a" * 64,
            "orzmc-windows-arm64.zip": "b" * 64,
        }

    def test_magic_rejects_html(self, tmp_path) -> None:
        page = tmp_path / "page"
        page.write_bytes(HTML_ERROR)
        assert not looks_like_executable(str(page), is_windows=False)

        elf = tmp_path / "bin"
        elf.write_bytes(SAFE_ELF)
        assert looks_like_executable(str(elf), is_windows=False)

        exe = tmp_path / "orzmc.exe"
        exe.write_bytes(b"MZ" + b"\x00" * 60)
        # PE 魔数只在 Windows 形态下接受。
        assert looks_like_executable(str(exe), is_windows=True)
        assert not looks_like_executable(str(exe), is_windows=False)


# ── 安装 ──────────────────────────────────────────────────────────────────────


class TestInstall:
    def test_downloads_verifies_and_writes_record(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses["/releases/latest"] = {"tag_name": "v0.18.0"}
        http.text_responses["SHA256SUMS"] = _sums("orzmc-linux-x86_64", SAFE_ELF)
        http.canned["orzmc-linux-x86_64"] = SAFE_ELF
        channel = _channel(tmp_path, http)

        state = channel.install()

        assert state.installed is True
        assert state.version == "0.18.0"
        assert state.path.endswith(os.path.join("bin", "orzmc"))
        with open(state.path, "rb") as handle:
            assert handle.read() == SAFE_ELF
        assert os.access(state.path, os.X_OK), "POSIX 上必须可执行"

        record = (tmp_path / "state" / "orzmc" / "install.conf").read_text(encoding="utf-8")
        assert "channel=wrapper" in record, "规范 §10.1:记录要标明这是 wrapper 通道"
        assert f"binary={state.path}" in record
        assert "source=github:OrzMC/OrzMCKit" in record

    def test_checksum_mismatch_never_lands(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses["/releases/latest"] = {"tag_name": "v0.18.0"}
        http.text_responses["SHA256SUMS"] = _sums("orzmc-linux-x86_64", b"expected-bytes")
        http.canned["orzmc-linux-x86_64"] = SAFE_ELF
        channel = _channel(tmp_path, http)

        with pytest.raises(RuntimeChannelError) as excinfo:
            channel.install()

        assert excinfo.value.code == "E_VERIFY"
        assert not os.path.exists(channel.path), "校验失败不得落盘"

    def test_missing_asset_reports_available_names(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses["/releases/latest"] = {"tag_name": "v0.18.0"}
        http.text_responses["SHA256SUMS"] = _sums("orzmc-linux-arm64", SAFE_ELF)
        channel = _channel(tmp_path, http)

        with pytest.raises(RuntimeChannelError) as excinfo:
            channel.install()

        assert excinfo.value.code == "E_NOT_FOUND"
        assert "orzmc-linux-arm64" in str(excinfo.value)

    def test_windows_zip_is_unpacked_with_runtime_dlls(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses["/releases/latest"] = {"tag_name": "v0.18.0"}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("orzmc.exe", b"MZ" + b"\x00" * 60)
            bundle.writestr("swiftCore.dll", b"DLL")
        payload = buffer.getvalue()
        http.text_responses["SHA256SUMS"] = _sums("orzmc-windows-arm64.zip", payload)
        http.canned["orzmc-windows-arm64.zip"] = payload
        channel = _channel(tmp_path, http, windows=True)

        state = channel.install()

        assert state.path.endswith("orzmc.exe")
        # DLL 必须与 exe 同行(缺了会 STATUS_DLL_NOT_FOUND)。
        assert (pathlib.Path(state.path).parent / "swiftCore.dll").is_file()

    def test_offline_without_cached_payload_is_network_error(self, tmp_path) -> None:
        http = FakeHttp()  # 没有任何种子 → 无 API、无 302
        channel = _channel(tmp_path, http)
        with pytest.raises(RuntimeChannelError) as excinfo:
            channel.install()
        assert excinfo.value.code == "E_NETWORK"


# ── 与原生安装共存 (§10.1) ───────────────────────────────────────────────────


def _write_record(tmp_path, *, binary: str, source: str, version: str = "0.18.0") -> None:
    state = tmp_path / "state" / "orzmc"
    state.mkdir(parents=True, exist_ok=True)
    (state / "install.conf").write_text(
        "\n".join(
            [
                "tool=orzmc",
                "schema=1",
                f"version={version}",
                "platform=linux-x86_64",
                f"install_dir={os.path.dirname(binary)}",
                f"binary={binary}",
                f"source={source}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class TestCoexistence:
    def test_reuses_existing_native_install(self, tmp_path) -> None:
        """已有原生 Swift 安装 -> 直接复用它, **不下载** (FakeHttp 没有种子, 一联网就炸)."""
        native = tmp_path / "bin" / "orzmc"
        native.parent.mkdir(parents=True)
        native.write_bytes(SAFE_ELF)
        native.chmod(0o755)
        _write_record(tmp_path, binary=str(native), source="github:OrzMC/OrzMCKit")
        channel = _channel(tmp_path, FakeHttp())

        path, source = channel.resolve()
        assert path == str(native)
        assert "OrzMCKit" in source
        assert channel.ensure() == str(native), "保留原生真身,不另存一份"

        state = channel.status().to_dict()
        assert state["reused"] is True
        assert state["path"] == str(native)
        assert state["version"] == "0.18.0"

    def test_install_is_a_noop_when_native_install_exists(self, tmp_path) -> None:
        native = tmp_path / "bin" / "orzmc"
        native.parent.mkdir(parents=True)
        native.write_bytes(SAFE_ELF)
        native.chmod(0o755)
        _write_record(tmp_path, binary=str(native), source="github:OrzMC/OrzMCKit")
        http = FakeHttp()  # 任何联网都会失败
        channel = _channel(tmp_path, http)

        state = channel.install()

        assert state.path == str(native)
        assert http.requests == [], f"不该发任何请求:{http.requests}"
        assert not (tmp_path / "state" / "orzmc" / "bin" / "orzmc").exists(), "不该多存一份副本"

    def test_never_reuses_python_binary(self, tmp_path) -> None:
        """`source` 指向 OrzPythonMC(Python 实现)时**不能**复用——那会自己转自己。"""
        python_bin = tmp_path / "venv" / "orzmc"
        python_bin.parent.mkdir(parents=True)
        python_bin.write_bytes(SAFE_ELF)
        python_bin.chmod(0o755)
        _write_record(tmp_path, binary=str(python_bin), source="github:OrzMC/OrzPythonMC")
        channel = _channel(tmp_path, FakeHttp())

        path, _ = channel.resolve()
        assert path != str(python_bin)
        assert path.endswith(os.path.join("bin", "orzmc"))
        assert channel.status().to_dict()["reused"] is False

    def test_dangling_native_record_falls_back_to_own_copy(self, tmp_path) -> None:
        _write_record(tmp_path, binary=str(tmp_path / "gone" / "orzmc"), source="github:OrzMC/OrzMCKit")
        channel = _channel(tmp_path, FakeHttp())
        path, _ = channel.resolve()
        assert path == channel.path
        assert channel.status().to_dict()["reused"] is False


# ── 转发 ──────────────────────────────────────────────────────────────────────


class TestForward:
    def test_ensure_reuses_installed_payload(self, tmp_path) -> None:
        http = FakeHttp()
        channel = _channel(tmp_path, http)
        os.makedirs(os.path.dirname(channel.path), exist_ok=True)
        with open(channel.path, "wb") as handle:
            handle.write(SAFE_ELF)

        # 已装就不该联网(FakeHttp 没有种子,一联网就抛)。
        assert channel.ensure() == channel.path

    def test_status_reports_wrapper_version_separately(self, tmp_path) -> None:
        from orzmc.version import __version__

        http = FakeHttp()
        channel = _channel(tmp_path, http)
        data = channel.status().to_dict()
        # 没有 install.conf = pip 新装 → 默认判定为 swift(规范 §10.1)。
        assert data["enabled"] is True
        assert data["installed"] is False
        # 版本号语义:真身版本与 launcher 版本分开报(规范 §10.1)。
        assert data["wrapper"] == __version__ == channel.status().to_dict()["wrapper"]
        assert data["version"] == ""
