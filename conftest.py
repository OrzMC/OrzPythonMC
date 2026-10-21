"""Worktree-wide pytest defaults (covers both ``orzmc/tests`` and ``orzmc_app/tests``)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs on the Python implementation, whatever this machine has installed.

    未设 ``ORZMC_RUNTIME`` 时 :func:`orzmc.services.runtime.resolve_runtime` 会读**本机**
    状态目录的 ``install.conf``:开发者只要跑过 ``orzmc runtime install``(或装过 Swift 原生版),
    ``orzmc_app.cli.main()`` 就会把整条 argv 交给 Swift 真身,而 POSIX 下那是 ``os.execv``
    —— **pytest 进程被整个替换**,表现为退出码 1、没有 traceback、连 summary 都没有,
    单个用例凭空"消失"(2026-10-22 本机实测;首例 ``test_usage_error_exit_code_is_one``)。

    CI 一直是钉住的(``ci.yml`` 的工作流级 ``env`` 就是 ``ORZMC_RUNTIME=python``),现在本地
    也一样:测试结果不该取决于"这台机器上装没装过实验通道"。需要验证转发/判定的用例
    自己 ``monkeypatch.setenv``,fixture 先跑、用例后设,互不影响。
    """
    monkeypatch.setenv("ORZMC_RUNTIME", "python")
