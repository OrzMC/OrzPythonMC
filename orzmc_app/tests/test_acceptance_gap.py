"""验收 harness 与实现之间的契约:上游缺包应判成 UP(gap),而不是 FAIL。

背景(2026-10-22):夜间验收在 `windows-11-arm` 连红 7 天,两侧各有一个缺口:

1. 实现侧把 Adoptium 的 404 当成「流式中途断连」重试 4 次,最终报
   「下载中途多次断开」—— 4 次无谓请求,真实原因(上游没有这个构建)被掩盖
   (已修:`HttpClient.stream_range` 对 4xx/5xx 立即抛 ``HttpStatusError``);
2. 判定侧靠 requests 的原文做标记,而 rich 在无 tty 时会软换行把 URL 切开,
   旧实现只把空白压成一个空格 —— 断点落在标记内部时仍然匹配不到
   (已修:`log_contains` 两侧都去掉全部空白后再比)。

这个测试把两件事绑在一起守:实现的消息里必须留着标记文本,且断行不能影响匹配。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from orzmc.domain.paths import PathLayout
from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient, HttpStatusError
from orzmc.infra.log import NullReporter
from orzmc.infra.progress import NullProgress
from orzmc.services.java import JavaEnv

ROOT = Path(__file__).resolve().parents[2]


def _harness() -> Any:
    """Load the acceptance harness from the checkout (needs ``psutil``)."""
    pytest.importorskip("psutil")
    spec = importlib.util.spec_from_file_location("orzmc_acceptance_harness", ROOT / "scripts" / "acceptance.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 解析需要模块已注册
    spec.loader.exec_module(module)
    return module


class _NoBuild:
    """Adoptium 上没有该 OS/arch/major 的构建:所有下载入口都 404。"""

    def _raise(self, url: str) -> None:
        raise HttpStatusError(f"404 Client Error: Not Found for url: {url}", status=404, url=url)

    def download(self, url: str, dest_path: str, **kwargs: Any) -> int:
        self._raise(url)
        return 0

    def stream_range(self, url: str, start: int, end: int | None, dest_path: str, **kwargs: Any) -> Any:
        self._raise(url)
        return None


def test_upstream_404_is_a_platform_gap_not_a_failure(tmp_path: Path) -> None:
    env = JavaEnv(
        cast(HttpClient, _NoBuild()), FileStore(), NullReporter(), NullProgress(), PathLayout(root=str(tmp_path))
    )
    with pytest.raises(RuntimeError) as info:
        env.resolve(25)
    message = str(info.value)
    # 面向用户的说明:是上游缺包,不是网络问题
    assert "Temurin 没有 Java 25 的" in message
    assert "HTTP 404" in message

    # rich 软换行会把长行切开(断点可能落在标记内部);harness 必须仍然认出来
    log = tmp_path / "case.log"
    log.write_text("\n".join(message[index : index + 76] for index in range(0, len(message), 76)), encoding="utf-8")
    acceptance = _harness()
    assert acceptance.log_contains(log, acceptance.GAP_MARKS)
