"""CLI smoke tests: subcommand routing, help tree, validation (no network)."""

from __future__ import annotations

import os
import sys

from typer.testing import CliRunner

from orzmc import DEFAULT_DOWNLOAD_THREADS, MAX_DOWNLOAD_THREADS, FileStore, UpdateCheck
from orzmc import __version__ as lib_version
from orzmc_app import __version__
from orzmc_app.cli import app

# ``orzmc_app.cli.app`` 包属性被 __init__ 重导出遮蔽成 Typer 实例,
# ``import ... as`` 走属性查找也会拿到实例;patch 必须落在真正的模块上。
app_module = sys.modules["orzmc_app.cli.app"]

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_app_and_library_versions_match() -> None:
    """库与应用一起发版:两处 ``__version__`` 必须同步(防发版漏改)。"""
    assert __version__ == lib_version


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("client", "server", "remove", "update", "list", "backup", "version", "self-uninstall"):
        assert name in result.stdout


def test_list_empty(tmp_path) -> None:
    result = runner.invoke(app, ["list", "--root-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "尚未安装任何版本" in result.stdout


def test_list_shows_installed(tmp_path) -> None:
    fs = FileStore()
    fs.ensure_dir(os.path.join(str(tmp_path), "versions", "1.20.4", "client"))
    fs.ensure_dir(os.path.join(str(tmp_path), "versions", "1.20.4", "server", "paper"))
    result = runner.invoke(app, ["list", "--root-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "1.20.4" in result.stdout
    assert "paper" in result.stdout


def test_client_rejects_server_type() -> None:
    result = runner.invoke(app, ["client", "-t", "paper", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "不能用于客户端" in result.stdout


def test_client_rejects_unknown_type() -> None:
    result = runner.invoke(app, ["client", "-t", "spigot", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "未知类型" in result.stdout


def test_client_accepts_forge_type(monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        return version or "1.20.4"

    def fake_launch(options, **kwargs) -> None:
        calls.append("launch")

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "launch_client", fake_launch)
    result = runner.invoke(app, ["client", "-t", "forge", "--version", "1.20.4"])
    assert result.exit_code == 0, result.stdout
    assert calls == ["launch"]


def test_client_passes_explicit_username(monkeypatch) -> None:
    usernames: list[str] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        return version or "1.20.4"

    def fake_launch(options, **kwargs) -> None:
        usernames.append(options.username)

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "launch_client", fake_launch)
    result = runner.invoke(app, ["client", "--version", "1.20.4", "-u", "Steve"])
    assert result.exit_code == 0, result.stdout
    assert usernames == ["Steve"]


def test_client_defaults_username_to_guest_when_not_tty(monkeypatch) -> None:
    """CliRunner 非 TTY → 真实 resolve_username 静默用默认 guest,不弹询问。"""
    usernames: list[str] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        return version or "1.20.4"

    def fake_launch(options, **kwargs) -> None:
        usernames.append(options.username)

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "launch_client", fake_launch)
    result = runner.invoke(app, ["client", "--version", "1.20.4"])
    assert result.exit_code == 0, result.stdout
    assert usernames == ["guest"]


def test_client_calls_resolve_username(monkeypatch) -> None:
    """未指定 -u → resolve_username(None) 被调用,返回值透传进 launch_client。"""
    calls: list[object] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        return version or "1.20.4"

    def fake_username(username):
        calls.append(username)
        return "Alice"

    def fake_launch(options, **kwargs) -> None:
        pass

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "resolve_username", fake_username)
    monkeypatch.setattr(app_module, "launch_client", fake_launch)
    result = runner.invoke(app, ["client", "--version", "1.20.4"])
    assert result.exit_code == 0, result.stdout
    assert calls == [None]


def test_server_accepts_fabric_and_forge(monkeypatch) -> None:
    types_seen: list[str] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        return version or "1.20.4"

    def fake_deploy(options, **kwargs) -> None:
        types_seen.append(options.game_type)

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "deploy_server", fake_deploy)
    for game_type in ("vanilla", "paper", "fabric", "forge"):
        result = runner.invoke(app, ["server", "-t", game_type, "--version", "1.20.4"])
        assert result.exit_code == 0, result.stdout
    assert types_seen == ["vanilla", "paper", "fabric", "forge"]


def test_refresh_flag_reaches_resolve_and_options(monkeypatch) -> None:
    """--refresh must reach both the picker's catalog and RuntimeOptions."""
    resolves: list[bool] = []
    client_seen: list[bool] = []
    server_seen: list[bool] = []

    def fake_resolve(version, root_dir=None, refresh=False):
        resolves.append(refresh)
        return version or "1.20.4"

    monkeypatch.setattr(app_module, "resolve_version", fake_resolve)
    monkeypatch.setattr(app_module, "resolve_username", lambda username: username or "guest")
    monkeypatch.setattr(app_module, "launch_client", lambda options, **kwargs: client_seen.append(options.refresh))
    monkeypatch.setattr(app_module, "deploy_server", lambda options, **kwargs: server_seen.append(options.refresh))

    client = runner.invoke(app, ["client", "--version", "1.20.4", "--refresh"])
    assert client.exit_code == 0, client.stdout
    server = runner.invoke(app, ["server", "--version", "1.20.4", "--refresh"])
    assert server.exit_code == 0, server.stdout
    assert resolves == [True, True]
    assert client_seen == [True]
    assert server_seen == [True]


def test_refresh_defaults_to_off(monkeypatch) -> None:
    seen: list[bool] = []

    monkeypatch.setattr(app_module, "resolve_version", lambda version, root_dir=None, refresh=False: version)
    monkeypatch.setattr(app_module, "resolve_username", lambda username: username or "guest")
    monkeypatch.setattr(app_module, "launch_client", lambda options, **kwargs: seen.append(options.refresh))
    result = runner.invoke(app, ["client", "--version", "1.20.4"])
    assert result.exit_code == 0, result.stdout
    assert seen == [False]


class TestUpdateCommand:
    """`orzmc update` wiring: check reporting, flag passthrough, cancel, errors."""

    def test_check_reports_both_versions(self, monkeypatch) -> None:
        seen: list[tuple] = []

        def fake_check(binary, version=None, reporter=None):
            seen.append((binary, version))
            return UpdateCheck(
                current="v1.0.0", latest="v2.0.0", asset="orzmc-x", platform="linux-x86_64", available=True
            )

        monkeypatch.setattr(app_module, "check_self_update", fake_check)
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0, result.stdout
        assert "v1.0.0" in result.stdout and "v2.0.0" in result.stdout
        assert "有新版本可用" in result.stdout
        assert seen[0][1] is None

    def test_check_reports_up_to_date(self, monkeypatch) -> None:
        monkeypatch.setattr(
            app_module,
            "check_self_update",
            lambda binary, version=None, reporter=None: UpdateCheck(
                current="v2.0.0", latest="v2.0.0", asset="orzmc-x", platform="linux-x86_64", available=False
            ),
        )
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0, result.stdout
        assert "已是最新版本" in result.stdout

    def test_flags_reach_update_self(self, monkeypatch) -> None:
        calls: list[dict] = []

        def fake_update(binary, **kwargs):
            calls.append(kwargs)
            return UpdateCheck(
                current="v1.0.0", latest="v2.0.0", asset="orzmc-x", platform="linux-x86_64", applied=True
            )

        monkeypatch.setattr(app_module, "update_self", fake_update)
        result = runner.invoke(app, ["update", "--version", "v2.0.0", "--file", "/tmp/orzmc-new", "--yes", "--force"])
        assert result.exit_code == 0, result.stdout
        assert calls[0]["version"] == "v2.0.0"
        assert calls[0]["file"] == "/tmp/orzmc-new"
        assert calls[0]["yes"] is True
        assert calls[0]["force"] is True
        assert calls[0]["confirm"] is None  # non-TTY: never prompt

    def test_cancelled_upgrade_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(app_module, "update_self", lambda binary, **kwargs: None)
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0, result.stdout
        assert "已取消升级" in result.stdout

    def test_update_error_exits_nonzero(self, monkeypatch) -> None:
        def boom(binary, **kwargs):
            raise RuntimeError("检测到 pip 安装")

        monkeypatch.setattr(app_module, "update_self", boom)
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "检测到 pip 安装" in result.stdout

    def test_check_error_exits_nonzero(self, monkeypatch) -> None:
        def boom(binary, version=None, reporter=None):
            raise RuntimeError("GitHub API 已限流")

        monkeypatch.setattr(app_module, "check_self_update", boom)
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 1
        assert "GitHub API 已限流" in result.stdout


def test_remove_server_requires_type() -> None:
    result = runner.invoke(app, ["remove", "-v", "1.20.4", "--server"])
    assert result.exit_code == 1
    assert "需要指定类型" in result.stdout


def test_remove_missing_version_raises(tmp_path) -> None:
    result = runner.invoke(app, ["remove", "-v", "9.9.9", "--yes", "--root-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "未找到" in result.stdout


def test_bad_memory_rejected() -> None:
    result = runner.invoke(app, ["client", "--minmem", "huge", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "无效内存" in result.stdout


class TestDownloadThreadsOption:
    """``-j/--download-threads``:资源阶段是几千个小文件,并发是主要提速手段。"""

    def test_flag_reaches_client_and_server_options(self, monkeypatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(app_module, "resolve_version", lambda version, root_dir=None, refresh=False: version)
        monkeypatch.setattr(app_module, "resolve_username", lambda username: username or "guest")
        monkeypatch.setattr(app_module, "launch_client", lambda options, **kw: seen.append(options.download_threads))
        monkeypatch.setattr(app_module, "deploy_server", lambda options, **kw: seen.append(options.download_threads))

        assert runner.invoke(app, ["client", "-v", "1.20.4", "-j", "32"]).exit_code == 0
        assert runner.invoke(app, ["server", "-v", "1.20.4", "--download-threads", "24"]).exit_code == 0
        assert seen == [32, 24]

    def test_default_is_the_shared_constant(self, monkeypatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(app_module, "resolve_version", lambda version, root_dir=None, refresh=False: version)
        monkeypatch.setattr(app_module, "resolve_username", lambda username: username or "guest")
        monkeypatch.setattr(app_module, "launch_client", lambda options, **kw: seen.append(options.download_threads))
        assert runner.invoke(app, ["client", "-v", "1.20.4"]).exit_code == 0
        assert seen == [DEFAULT_DOWNLOAD_THREADS]

    def test_out_of_range_rejected(self) -> None:
        for value in ("0", "-1", str(MAX_DOWNLOAD_THREADS + 1)):
            result = runner.invoke(app, ["client", "-v", "1.20.4", "-j", value])
            assert result.exit_code == 1, value
            assert "下载并发数必须在" in result.stdout

    def test_help_documents_the_flag(self) -> None:
        result = runner.invoke(app, ["client", "--help"])
        assert "--download-threads" in result.stdout


def test_self_uninstall_calls_public_api(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_uninstall(binary, **kwargs) -> bool:
        calls.append((binary, kwargs))
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["self-uninstall", "--yes", "--force", "--root-dir", "/tmp/mc-root"])
    assert result.exit_code == 0, result.stdout
    assert calls
    binary, kwargs = calls[0]
    assert binary  # sys.argv[0] 解析后的绝对路径
    assert kwargs["yes"] is True
    assert kwargs["force"] is True
    assert kwargs["root_dir"] == "/tmp/mc-root"
    assert kwargs["remove_root"] is False


def test_self_uninstall_removes_root_when_flagged(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_uninstall(binary, **kwargs) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["self-uninstall", "--remove-root"])
    assert result.exit_code == 0, result.stdout
    assert calls[0]["remove_root"] is True


def test_self_uninstall_error_fails(monkeypatch) -> None:
    def fake_uninstall(binary, **kwargs) -> bool:
        raise RuntimeError("检测到疑似开发环境安装")

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["self-uninstall"])
    assert result.exit_code == 1
    assert "检测到疑似开发环境安装" in result.stdout


class TestForceUtf8Stdio:
    """_reconfigure_utf8 / _force_utf8_stdio:Windows 上管道输出中文不崩。"""

    def test_reconfigures_to_utf8_replace(self) -> None:
        calls: list[dict] = []

        class Fake:
            def reconfigure(self, **kw) -> None:
                calls.append(kw)

        from orzmc_app.cli import _reconfigure_utf8

        _reconfigure_utf8(Fake())
        assert calls == [{"encoding": "utf-8", "errors": "replace"}]

    def test_tolerates_missing_reconfigure(self) -> None:
        from orzmc_app.cli import _reconfigure_utf8

        _reconfigure_utf8(object())  # 无 reconfigure → no-op,不抛

    def test_tolerates_reconfigure_error(self) -> None:
        class Fake:
            def reconfigure(self, **kw) -> None:
                raise ValueError("closed stream")

        from orzmc_app.cli import _reconfigure_utf8

        _reconfigure_utf8(Fake())  # 出错被吞,不抛

    def test_force_utf8_stdio_noop_on_capture(self) -> None:
        """pytest 捕获模式下 sys.stdout 无 reconfigure,调用应安全 no-op。"""
        from orzmc_app.cli import _force_utf8_stdio

        _force_utf8_stdio()
