"""跨进程文件锁(`frontends.md` F4.1):`<stateDir>/auth.lock` 目录锁。

**算法必须与 Swift 侧逐字一致**,否则两边互不加锁。核心只有三点:

1. **获取**:`os.mkdir(lock)` —— 在 POSIX 与 Windows 上都是原子的;
2. **陈旧检测**:目录 `mtime` 超过 `stale_after`(默认 30s)视为持锁进程已崩,删掉重试;
3. **超时**:最多等 `timeout`(默认 10s),拿不到就报 ``E_AUTH_LOCKED``——
   **绝不**在没拿到锁的情况下进临界区(令牌刷新会在网络请求期间持有它)。

为什么不用 `flock`:`flock` 与 `fcntl` 锁在 Linux/macOS 上属于不同命名空间,跨语言极易"看起来加了锁,
其实互不可见";Windows 又没有 `flock`。`mkdir` 三平台语义一致,代价是崩溃后要陈旧检测兜底。
"""

from __future__ import annotations

import os
import time
from types import TracebackType

STALE_AFTER_SECONDS = 30.0
DEFAULT_TIMEOUT = 10.0
POLL_INTERVAL = 0.05


class LockTimeout(RuntimeError):
    """拿不到锁(规范错误码 ``E_AUTH_LOCKED``)。"""

    code = "E_AUTH_LOCKED"


def lock_path(state_dir: str) -> str:
    """锁目录 `<stateDir>/auth.lock`(**目录**,靠 `mkdir` 原子性)。"""
    return os.path.join(state_dir, "auth.lock")


def _age_seconds(path: str, now: float | None = None) -> float:
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return 0.0
    return (now if now is not None else time.time()) - mtime


def _is_stale(path: str, stale_after: float, now: float | None = None) -> bool:
    return _age_seconds(path, now) > stale_after


def _break_stale(path: str) -> None:
    """清掉陈旧锁目录(先删 owner,再 rmdir;失败就算了,下轮还会重试)。"""
    try:
        owner = os.path.join(path, "owner")
        if os.path.exists(owner):
            os.remove(owner)
        os.rmdir(path)
    except OSError:
        pass


class FileLock:
    """目录锁上下文管理器(`with FileLock(path): ...`)。"""

    def __init__(
        self,
        path: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        stale_after: float = STALE_AFTER_SECONDS,
        sleep=time.sleep,
        clock=time.time,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self._sleep = sleep
        self._clock = clock
        self._held = False

    # ── 获取 / 释放 ────────────────────────────────────────────────────────

    def acquire(self) -> None:
        deadline = self._clock() + self.timeout
        while True:
            try:
                os.mkdir(self.path)
            except FileExistsError:
                if _is_stale(self.path, self.stale_after, self._clock()):
                    _break_stale(self.path)
                    continue
                if self._clock() >= deadline:
                    raise LockTimeout(
                        f"拿不到认证锁 {self.path}(另一进程正在刷新令牌);稍后重试,或确认没有卡住的 orzmc 进程",
                    ) from None
                self._sleep(POLL_INTERVAL)
                continue
            except OSError as exc:  # 权限 / 磁盘满等:不该静默继续
                raise LockTimeout(f"无法创建认证锁 {self.path}: {exc}") from exc
            self._write_owner()
            self._held = True
            return

    def _write_owner(self) -> None:
        """写持有者信息(pid + 起始时间)——仅供排查,不参与判定。"""
        try:
            with open(os.path.join(self.path, "owner"), "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()}\nsince={_iso_now()}\n")
        except OSError:
            pass

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            owner = os.path.join(self.path, "owner")
            if os.path.exists(owner):
                os.remove(owner)
        except OSError:
            pass
        try:
            os.rmdir(self.path)
        except OSError:
            # 释放失败不抛:陈旧检测会在 30s 后兜底。
            pass

    # ── 上下文管理器 ──────────────────────────────────────────────────────

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _iso_now() -> str:
    """UTC ISO8601(秒精度)——写进 ``owner`` 供排查,不参与判定。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "DEFAULT_TIMEOUT",
    "POLL_INTERVAL",
    "STALE_AFTER_SECONDS",
    "FileLock",
    "LockTimeout",
    "lock_path",
]
