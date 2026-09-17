"""resolve_version wiring: explicit value / non-TTY / picker call / fallbacks, no TTY/network."""

from __future__ import annotations

from orzmc import VersionEntry
from orzmc_app.cli import picker as picker_module
from orzmc_app.cli import prompts as prompts_module
from orzmc_app.cli.prompts import resolve_username, resolve_version

CATALOG = [
    VersionEntry("1.21.4", "release"),
    VersionEntry("1.21.3", "release"),
    VersionEntry("1.20.4", "release"),
    VersionEntry("25w14a", "snapshot"),
    VersionEntry("b1.7.3", "old_beta"),
    VersionEntry("c0.0.13a", "old_alpha"),
]


class TestResolveVersion:
    def test_lazy_names_resolve_via_module_getattr(self, monkeypatch) -> None:
        """回归:惰性名必须经模块 __getattr__ 解析,而非函数内裸全局名。

        原实现在 resolve_version 里直接引用 remote_version_catalog(LOAD_GLOBAL)。
        模块 __getattr__ 只对 ``module.attr`` 式访问生效,裸全局名不触发 → 交互
        路径 NameError(frozen 真机报 name 'remote_version_catalog' is not defined)。
        本测试不预置 prompts 模块属性,只在源模块(orzmc / picker)打桩,
        强制走 __getattr__ 兜底 —— 旧代码下返回 None,本断言即失败。
        """
        import orzmc as orzmc_module

        monkeypatch.setattr(orzmc_module, "remote_version_catalog", lambda root_dir=None, refresh=False: CATALOG)
        monkeypatch.setattr(picker_module, "run_picker", lambda catalog: "1.21.4")
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        assert resolve_version(None, None) == "1.21.4"

    def test_explicit_version_skips_prompt(self) -> None:
        assert resolve_version("1.20.4", None) == "1.20.4"

    def test_non_interactive_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: False)
        assert resolve_version(None, None) is None

    def test_tty_calls_picker(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "remote_version_catalog", lambda root_dir=None, refresh=False: CATALOG)
        monkeypatch.setattr(prompts_module, "run_picker", lambda catalog: "1.21.4")
        assert resolve_version(None, None) == "1.21.4"

    def test_refresh_forwarded_to_catalog(self, monkeypatch) -> None:
        seen: list[bool] = []

        def catalog(root_dir=None, refresh=False):
            seen.append(refresh)
            return CATALOG

        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "remote_version_catalog", catalog)
        monkeypatch.setattr(prompts_module, "run_picker", lambda catalog: None)
        assert resolve_version(None, None, refresh=True) is None
        assert seen == [True]

    def test_picker_none_falls_back_to_latest(self, monkeypatch) -> None:
        # Escape in the picker returns None → caller falls back to latest release.
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "remote_version_catalog", lambda root_dir=None, refresh=False: CATALOG)
        monkeypatch.setattr(prompts_module, "run_picker", lambda catalog: None)
        assert resolve_version(None, None) is None

    def test_picker_exception_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "remote_version_catalog", lambda root_dir=None, refresh=False: CATALOG)

        def boom(catalog):
            raise RuntimeError("选择器故障")

        monkeypatch.setattr(prompts_module, "run_picker", boom)
        assert resolve_version(None, None) is None

    def test_network_failure_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)

        def boom(root_dir=None, refresh=False):
            raise RuntimeError("网络错误")

        monkeypatch.setattr(prompts_module, "remote_version_catalog", boom)
        assert resolve_version(None, None) is None

    def test_empty_catalog_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "remote_version_catalog", lambda root_dir=None, refresh=False: [])
        assert resolve_version(None, None) is None


class TestResolveUsername:
    """resolve_username: explicit flag / TTY ask / non-TTY default, no TTY needed."""

    def test_explicit_value_passthrough(self) -> None:
        assert resolve_username("Alice") == "Alice"

    def test_non_interactive_returns_guest(self, monkeypatch) -> None:
        monkeypatch.setattr(prompts_module, "is_interactive", lambda: False)
        monkeypatch.setattr(prompts_module, "Prompt", object())  # 若误调 ask → AttributeError
        assert resolve_username(None) == "guest"

    def test_tty_asks_and_returns_answer(self, monkeypatch) -> None:
        class _Prompt:
            @staticmethod
            def ask(prompt, default=None) -> str:
                return "Alice"

        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "Prompt", _Prompt)
        assert resolve_username(None) == "Alice"

    def test_tty_empty_input_returns_guest(self, monkeypatch) -> None:
        class _Prompt:
            @staticmethod
            def ask(prompt, default=None) -> str:
                return ""

        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "Prompt", _Prompt)
        assert resolve_username(None) == "guest"

    def test_tty_whitespace_stripped(self, monkeypatch) -> None:
        class _Prompt:
            @staticmethod
            def ask(prompt, default=None) -> str:
                return "  Bob  "

        monkeypatch.setattr(prompts_module, "is_interactive", lambda: True)
        monkeypatch.setattr(prompts_module, "Prompt", _Prompt)
        assert resolve_username(None) == "Bob"
