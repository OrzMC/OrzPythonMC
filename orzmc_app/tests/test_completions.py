"""补全落点与安装(规范 ``frontends.md`` F6、``cli-surface.md`` §9)。

这里全是纯函数 + 临时 HOME,不打网络、不碰真实 shell 配置。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orzmc_app.cli.completions import (
    PROG_NAME,
    SHELLS,
    UnsupportedInstall,
    install,
    normalize_shell,
    resolve_shell,
    target_path,
)


class TestNormalizeShell:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/bin/zsh", "zsh"),
            ("/usr/local/bin/fish", "fish"),
            (r"C:\Program Files\Git\bin\bash.exe", "bash"),
            ("PWSH.EXE", "pwsh"),
            ("/bin/tcsh", None),
            ("", None),
            (None, None),
        ],
    )
    def test_normalizes(self, raw: str | None, expected: str | None) -> None:
        assert normalize_shell(raw) == expected


class TestResolveShell:
    def test_posix_shell_env(self) -> None:
        assert resolve_shell({"SHELL": "/bin/zsh"}) == "zsh"

    def test_windows_powershell_without_shell_env(self) -> None:
        assert resolve_shell({}, is_windows=True) == "powershell"

    def test_psmodulepath_does_not_make_it_windows(self) -> None:
        # 装了 pwsh 的 Linux/macOS 也有 PSModulePath;据此判平台会把 Linux 误判成 Windows
        # (CI runner 真踩过:ubuntu 上 `completion install` 报成 powershell)。
        assert resolve_shell({"PSModulePath": "/x/Modules"}, is_windows=False) is None

    def test_unknown_shell_env_falls_through(self) -> None:
        assert resolve_shell({"SHELL": "/bin/tcsh"}, is_windows=False) is None

    def test_empty_env(self) -> None:
        assert resolve_shell({}, is_windows=False) is None


class TestTargetPath:
    def test_paths_match_spec(self, tmp_path: Path) -> None:
        assert target_path("bash", tmp_path) == (tmp_path / ".local/share/bash-completion/completions" / PROG_NAME)
        assert target_path("zsh", tmp_path) == tmp_path / ".zsh/completions" / f"_{PROG_NAME}"
        assert target_path("fish", tmp_path) == tmp_path / ".config/fish/completions" / f"{PROG_NAME}.fish"
        assert target_path("powershell", tmp_path) is None
        assert target_path("pwsh", tmp_path) is None

    def test_every_supported_shell_is_covered(self) -> None:
        for shell in SHELLS:
            assert (shell in {"powershell", "pwsh"}) == (target_path(shell, Path("/h")) is None)


class TestInstall:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_writes_script_and_is_idempotent(self, shell: str, tmp_path: Path) -> None:
        path = target_path(shell, tmp_path)
        assert path is not None
        first = install(shell, "# script\n", home=tmp_path)
        assert first.path == path
        assert path.read_text() == "# script\n"
        second = install(shell, "# script\n", home=tmp_path)
        assert second.path == path
        assert path.read_text() == "# script\n"

    def test_bash_wires_rc_once(self, tmp_path: Path) -> None:
        # 落点在 bash-completion 目录;没装 bash-completion(如 macOS bash 3.2)时
        # 只能靠 source,所以必须幂等地补一行 rc。
        install("bash", "# script\n", home=tmp_path)
        install("bash", "# script\n", home=tmp_path)
        rc = (tmp_path / ".bashrc").read_text()
        assert rc.count("added by orzmc") == 1

    def test_zsh_adds_fpath_once(self, tmp_path: Path) -> None:
        install("zsh", "# script\n", home=tmp_path)
        install("zsh", "# script\n", home=tmp_path)
        rc = (tmp_path / ".zshrc").read_text()
        assert rc.count("fpath+=(~/.zsh/completions)") == 1

    def test_zsh_leaves_existing_fpath_untouched(self, tmp_path: Path) -> None:
        rc = tmp_path / ".zshrc"
        rc.write_text("fpath+=(~/.zsh/completions)\n", encoding="utf-8")
        result = install("zsh", "# script\n", home=tmp_path)
        assert result.hint == ""
        assert rc.read_text() == "fpath+=(~/.zsh/completions)\n"

    def test_zsh_appends_after_missing_trailing_newline(self, tmp_path: Path) -> None:
        rc = tmp_path / ".zshrc"
        rc.write_text("export EDITOR=vim", encoding="utf-8")
        install("zsh", "# script\n", home=tmp_path)
        lines = rc.read_text().splitlines()
        assert lines[0] == "export EDITOR=vim"
        assert any("added by orzmc" in line for line in lines)

    @pytest.mark.parametrize("shell", ["powershell", "pwsh"])
    def test_script_only_shells_are_unsupported(self, shell: str, tmp_path: Path) -> None:
        # frontends.md F6:只生成脚本、不落盘 → 明确报不支持,而不是 ok:true + path:null。
        with pytest.raises(UnsupportedInstall):
            install(shell, "# script\n", home=tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_unknown_shell_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            install("tcsh", "# script\n", home=tmp_path)
