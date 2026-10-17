"""CLI smoke tests: subcommand routing, help tree, validation (no network)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, cast

import typer
from typer.testing import CliRunner

from orzmc import DEFAULT_DOWNLOAD_THREADS, MAX_DOWNLOAD_THREADS, FileStore, UpdateCheck, disclaimer_text
from orzmc import __version__ as lib_version
from orzmc._compliance import DISCLAIMER_EN, DISCLAIMER_ZH, TRADEMARK_ZH
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
    for name in (
        "client",
        "server",
        "remove",
        "update",
        "list",
        "backup",
        "version",
        "uninstall",
        "completion",
        "capabilities",
        "plugin",
        "app",
    ):
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


# ── --json 信封(规范 §6.1)────────────────────────────────────────────────────


def test_json_version_envelope() -> None:
    result = runner.invoke(app, ["--json", "version"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "command": "version",
        "data": {"tool": __version__, "library": lib_version, "disclaimer": disclaimer_text()},
        "error": None,
    }


def test_version_text_is_multi_line_and_email_free() -> None:
    """人类可读 `version`: 版本与声明分开、中英各占一行、不泄露邮箱。"""
    text = disclaimer_text()
    assert text.splitlines() == [DISCLAIMER_EN, DISCLAIMER_ZH, TRADEMARK_ZH]
    assert "@" not in text

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    out = result.stdout
    assert f"orzmc {__version__} (库 {lib_version})" in out
    assert f"{DISCLAIMER_EN}\n{DISCLAIMER_ZH}\n{TRADEMARK_ZH}" in out  # 中英不混排
    assert "@" not in out  # 声明不再带联系邮箱


def test_json_list_envelope(tmp_path) -> None:
    result = runner.invoke(app, ["--json", "--root-dir", str(tmp_path), "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True, "command": "list", "data": {"versions": []}, "error": None}


def test_json_error_envelope_and_exit_code() -> None:
    result = runner.invoke(app, ["--json", "client", "-t", "paper", "--version", "1.20.4"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "client"
    assert payload["data"] is None
    assert payload["error"]["code"] == "E_UNSUPPORTED_TYPE"


# ── completion(规范 §9)──────────────────────────────────────────────────────


def test_completion_script() -> None:
    result = runner.invoke(app, ["completion", "bash"])
    assert result.exit_code == 0
    assert "COMPREPLY" in result.stdout
    assert "complete_bash" in result.stdout


def test_completion_json_envelope() -> None:
    result = runner.invoke(app, ["--json", "completion", "zsh"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["shell"] == "zsh"
    assert payload["data"]["script"]


def test_completion_rejects_unknown_shell() -> None:
    result = runner.invoke(app, ["completion", "tcsh"])
    assert result.exit_code == 1


# ── capabilities(规范 §8)────────────────────────────────────────────────────


def test_capabilities_json_envelope() -> None:
    result = runner.invoke(app, ["--json", "capabilities"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    data = payload["data"]
    names = {command["name"] for command in data["commands"]}
    assert {"client", "server", "list", "capabilities"} <= names
    assert any(entry["code"] == 130 for entry in data["exitCodes"])


def test_capabilities_lists_options() -> None:
    result = runner.invoke(app, ["--json", "capabilities"])
    data = json.loads(result.stdout)["data"]
    client = next(command for command in data["commands"] if command["name"] == "client")
    assert any("--version" in opt["names"] for opt in client["options"])


def test_capabilities_reports_supported_enums() -> None:
    """`capabilities` 必须上报**实际支持**的枚举值(父仓库 surface 校验据此比对)。"""
    result = runner.invoke(app, ["--json", "capabilities"])
    data = json.loads(result.stdout)["data"]
    by_name = {command["name"]: command for command in data["commands"]}

    def enum_of(command: str, option: str) -> list[str] | None:
        entry = next(o for o in by_name[command]["options"] if option in o["names"])
        return entry["enum"]

    assert enum_of("client", "--type") == ["vanilla", "fabric", "forge"]
    assert enum_of("server", "--type") == ["vanilla", "paper", "fabric", "forge"]
    assert enum_of("update", "--channel") == ["release", "beta"]
    assert enum_of("completion", "target") == [
        "bash",
        "zsh",
        "fish",
        "powershell",
        "pwsh",
        "install",
    ]
    # App Store 渠道未上架: 只声明 release(规范目标里还有 appstore, 属已声明差异)。
    assert enum_of("app", "--channel") == ["release"]
    # 非枚举选项必须为 null, 避免误报。
    assert enum_of("client", "--username") is None


def test_client_rejects_server_type() -> None:
    result = runner.invoke(app, ["client", "-t", "paper", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "不能用于客户端" in result.stderr


def test_client_rejects_unknown_type() -> None:
    result = runner.invoke(app, ["client", "-t", "spigot", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "未知类型" in result.stderr


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

        def fake_check(binary, version=None, channel=None, reporter=None):
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
            lambda binary, version=None, channel=None, reporter=None: UpdateCheck(
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
        assert "检测到 pip 安装" in result.stderr

    def test_check_error_exits_nonzero(self, monkeypatch) -> None:
        def boom(binary, version=None, channel=None, reporter=None):
            raise RuntimeError("GitHub API 已限流")

        monkeypatch.setattr(app_module, "check_self_update", boom)
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 1
        assert "GitHub API 已限流" in result.stderr


def test_remove_server_requires_type() -> None:
    result = runner.invoke(app, ["remove", "-v", "1.20.4", "--server"])
    assert result.exit_code == 1
    assert "需要指定类型" in result.stderr


def test_remove_missing_version_raises(tmp_path) -> None:
    result = runner.invoke(app, ["remove", "-v", "9.9.9", "--yes", "--root-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "未找到" in result.stderr


def test_bad_memory_rejected() -> None:
    result = runner.invoke(app, ["client", "--minmem", "huge", "--version", "1.20.4"])
    assert result.exit_code == 1
    assert "无效内存" in result.stderr


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
            assert "下载并发数必须在" in result.stderr

    def test_help_documents_the_flag(self) -> None:
        # 不断言渲染后的帮助文本:控制台宽度不同,rich 可能把长选项名截断成
        # "--download-threa…"(CI 上就因此假红过)。断言命令元数据更可靠。
        # click 不是本项目的直接依赖(typer 内部自带),所以只用 Any 起类型作用。
        group = cast("Any", typer.main.get_command(app))
        opts = {opt for param in group.commands["client"].params for opt in param.opts}
        assert {"--download-threads", "-j"} <= opts


def test_uninstall_calls_public_api(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_uninstall(binary, **kwargs) -> bool:
        calls.append((binary, kwargs))
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["uninstall", "--yes", "--force", "--root-dir", "/tmp/mc-root"])
    assert result.exit_code == 0, result.stdout
    assert calls
    binary, kwargs = calls[0]
    assert binary  # sys.argv[0] 解析后的绝对路径
    assert kwargs["yes"] is True
    assert kwargs["force"] is True
    assert kwargs["root_dir"] == "/tmp/mc-root"
    assert kwargs["remove_root"] is False


def test_uninstall_removes_root_when_flagged(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_uninstall(binary, **kwargs) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["uninstall", "--yes", "--remove-root"])
    assert result.exit_code == 0, result.stdout
    assert calls[0]["remove_root"] is True


def test_uninstall_error_fails(monkeypatch) -> None:
    def fake_uninstall(binary, **kwargs) -> bool:
        raise RuntimeError("检测到疑似开发环境安装")

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["uninstall", "--yes"])
    assert result.exit_code == 1
    assert "检测到疑似开发环境安装" in result.stderr


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


# ── plugin / app(离线:patch 公共 API 类)────────────────────────────────────


def test_plugin_search_json(monkeypatch) -> None:
    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def search(self, text: str, limit: int) -> list[dict[str, Any]]:
            return [{"name": "LuckPerms", "description": "perms", "stats": {"downloads": 7, "stars": 1}}]

    monkeypatch.setattr(app_module, "PluginService", FakeService)
    result = runner.invoke(app, ["--json", "plugin", "--search", "luck"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["mode"] == "search"
    assert payload["data"]["count"] == 1
    assert payload["data"]["results"][0]["name"] == "LuckPerms"


def test_plugin_requires_output() -> None:
    result = runner.invoke(app, ["plugin"])
    assert result.exit_code == 1


def test_app_status_json(monkeypatch) -> None:
    from orzmc import AppStatus

    class FakeInstaller:
        def __init__(self, **kwargs: Any) -> None:
            pass

        installed_path = "/tmp/OrzMC.app"

        def status(self) -> AppStatus:
            return AppStatus(installed=False, path="/tmp/OrzMC.app", version=None)

    monkeypatch.setattr(app_module, "AppInstaller", FakeInstaller)
    result = runner.invoke(app, ["--json", "app", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["installed"] is False


def test_app_unknown_action(monkeypatch) -> None:
    class FakeInstaller:
        def __init__(self, **kwargs: Any) -> None:
            pass

        installed_path = "/tmp/OrzMC.app"

    monkeypatch.setattr(app_module, "AppInstaller", FakeInstaller)
    result = runner.invoke(app, ["app", "bogus"])
    assert result.exit_code == 1


def test_fail_cancelled_exits_130() -> None:
    """规范 §6: 用户取消 (E_CANCELLED) 退出码 130,其余 1。"""
    import pytest

    with pytest.raises(typer.Exit) as cancelled:
        app_module._fail("已取消", code="E_CANCELLED")
    assert cancelled.value.exit_code == 130

    with pytest.raises(typer.Exit) as usage:
        app_module._fail("参数错误", code="E_USAGE")
    assert usage.value.exit_code == 1


def test_client_auth_without_client_id(monkeypatch) -> None:
    """`--auth` 无 client_id(未内置且未设环境变量)→ E_AUTH_NOT_CONFIGURED(不联网)。"""
    monkeypatch.setattr(app_module, "resolve_client_id", lambda override=None: "")
    result = runner.invoke(app, ["client", "--auth"])
    assert result.exit_code == 1
    assert "client_id" in result.stderr


def test_client_auth_without_client_id_json(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "resolve_client_id", lambda override=None: "")
    result = runner.invoke(app, ["--json", "client", "--auth"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_AUTH_NOT_CONFIGURED"


def test_server_kill_all_json(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "kill_all_servers", lambda root_dir=None: [111, 222])
    result = runner.invoke(app, ["--json", "server", "--kill-all"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"mode": "kill-all", "killed": [111, 222]}


# ── 规范对齐:帮助选项 / 退出码 / 取消语义 / stdout 纯净 ─────────────────────


def test_short_help_flag_everywhere() -> None:
    """规范 §3 + ``cli-surface.json`` globalOptions:``-h`` 必须与 ``--help`` 等价。"""
    for args in (["-h"], ["--help"], ["client", "-h"], ["server", "-h"], ["completion", "-h"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.stdout)
        assert "Usage" in result.stdout or "用法" in result.stdout


def test_typer_completion_options_are_not_exposed() -> None:
    """`add_completion=True` 只保留“补全类注册”,不暴露 typer 自带那两个重复选项。"""
    group = cast("Any", typer.main.get_command(app))
    names = {opt for param in group.params for opt in getattr(param, "opts", [])}
    assert "--install-completion" not in names
    assert "--show-completion" not in names
    # 但动态补全(``completion <shell>`` 生成的脚本依赖它)必须还活着。
    result = runner.invoke(
        app,
        [],
        env={"_ORZMC_COMPLETE": "complete_bash", "COMP_WORDS": "orzmc cl", "COMP_CWORD": "1"},
    )
    assert "client" in result.stdout


def test_usage_error_exit_code_is_one(monkeypatch) -> None:
    """规范 §6 的退出码只有 0/1/130;click 默认给 2,``main()`` 收敛成 1。"""
    cli_main = sys.modules["orzmc_app.cli"]

    monkeypatch.setattr(sys, "argv", ["orzmc", "nope"])
    assert cli_main.main() == 1


def test_remove_without_yes_is_cancelled(tmp_path) -> None:
    """规范 §8.2:缺 ``--yes`` 的非交互 ``remove`` 返回取消(130),且什么都没删。"""
    target = tmp_path / "versions" / "1.20.4" / "client"
    target.mkdir(parents=True)
    result = runner.invoke(app, ["--json", "remove", "-v", "1.20.4", "--root-dir", str(tmp_path)])
    assert result.exit_code == 130
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_CANCELLED"
    assert target.is_dir()


def test_uninstall_without_yes_is_cancelled(monkeypatch) -> None:
    """``frontends.md`` F2:卸载必须保留二次确认 —— 非交互需显式 ``--yes``。"""
    called: list[int] = []

    def fake_uninstall(*args: object, **kwargs: object) -> bool:
        called.append(1)
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["--json", "uninstall"])
    assert result.exit_code == 130
    assert called == []
    assert json.loads(result.stdout)["error"]["code"] == "E_CANCELLED"


def test_uninstall_json_keeps_stdout_clean(monkeypatch) -> None:
    """规范 §6.1:stdout 只有一个信封 —— 库日志必须走 stderr。"""

    def fake_uninstall(binary, **kwargs):
        kwargs["reporter"].info("NOISE-FROM-LIBRARY")
        return True

    monkeypatch.setattr(app_module, "uninstall_self", fake_uninstall)
    result = runner.invoke(app, ["--json", "uninstall", "--yes"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # 整段 stdout 必须是单个 JSON
    assert payload["data"] == {"uninstalled": True}
    assert "NOISE-FROM-LIBRARY" not in result.stdout


# ── completion install(规范 §9 / frontends F6)──────────────────────────────


def _sandbox_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    return home


def test_completion_install_uses_env_shell(monkeypatch, tmp_path) -> None:
    home = _sandbox_home(monkeypatch, tmp_path)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PSModulePath", raising=False)
    result = runner.invoke(app, ["--json", "completion", "install"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["shell"] == "zsh"
    assert payload["data"]["path"] == str(home / ".zsh/completions/_orzmc")
    assert (home / ".zsh/completions/_orzmc").is_file()
    # 幂等:再装一次不重复改 rc
    assert runner.invoke(app, ["completion", "install"]).exit_code == 0
    assert (home / ".zshrc").read_text().count("fpath+=(~/.zsh/completions)") == 1


def test_completion_install_without_shell_is_usage_error(monkeypatch, tmp_path) -> None:
    if os.name == "nt":  # pragma: no cover - Windows 总会兜底 powershell
        import pytest

        pytest.skip("Windows 上 resolve_shell 总会兜底 powershell")
    _sandbox_home(monkeypatch, tmp_path)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.delenv("PSModulePath", raising=False)
    result = runner.invoke(app, ["--json", "completion", "install"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "E_USAGE"
    assert "completion <shell>" in payload["error"]["message"]


def test_completion_install_registers_file_for_uninstall(monkeypatch, tmp_path) -> None:
    """F2/§9:装完补全要登记进 install.conf,`uninstall` 才清得掉。"""
    home = _sandbox_home(monkeypatch, tmp_path)
    seen: list[str] = []
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PSModulePath", raising=False)
    monkeypatch.setattr(app_module, "record_completion_file", lambda path: seen.append(path))
    assert runner.invoke(app, ["completion", "install"]).exit_code == 0
    assert seen == [str(home / ".zsh/completions/_orzmc")]


def test_completion_install_powershell_is_unsupported(monkeypatch, tmp_path) -> None:
    """F6:powershell/pwsh 只生成脚本 → `install` 明确返回 E_UNSUPPORTED。"""
    _sandbox_home(monkeypatch, tmp_path)
    for shell in ("powershell", "pwsh"):
        result = runner.invoke(app, ["--json", "completion", "install"], env={"SHELL": shell})
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["error"]["code"] == "E_UNSUPPORTED"
        assert "completion " + shell in payload["error"]["message"]
