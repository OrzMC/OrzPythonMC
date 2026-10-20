"""跨进程锁 (`frontends.md` F4.1) 与「刷新持锁」的单测。

锁是**跨语言契约**:算法与 Swift 侧逐字一致(`<stateDir>/auth.lock` 目录锁),
所以这里既测算法本身(获取/争用/陈旧/超时/释放),也测 `Authenticator.ensure()` 的临界区语义
(锁内重读、别人刚刷完就不重复刷)。
"""

from __future__ import annotations

import os
import time

import pytest

from orzmc.infra import auth_store as auth_store_module
from orzmc.infra.auth_store import AuthStore
from orzmc.infra.filelock import FileLock, LockTimeout, lock_path
from orzmc.infra.fs import FileStore


def _clock_of(start: float = 1000.0):
    """可控时钟:每次调用前进 ``tick``,用来把超时/陈旧判定跑快。"""
    state = {"now": start}

    def clock() -> float:
        return state["now"]

    def advance(seconds: float) -> None:
        state["now"] += seconds

    def sleep(seconds: float) -> None:
        state["now"] += max(seconds, 0.001)

    return clock, advance, sleep


class TestFileLock:
    def test_acquire_creates_directory_and_owner(self, tmp_path) -> None:
        path = str(tmp_path / "auth.lock")
        with FileLock(path):
            assert os.path.isdir(path), "锁用**目录**表示(mkdir 原子)"
            owner = (tmp_path / "auth.lock" / "owner").read_text(encoding="utf-8")
            assert f"pid={os.getpid()}" in owner
            assert "since=" in owner
        assert not os.path.exists(path), "退出上下文必须释放"

    def test_second_holder_waits_until_released(self, tmp_path) -> None:
        path = str(tmp_path / "auth.lock")
        clock, _advance, sleep = _clock_of()
        first = FileLock(path, clock=clock, sleep=sleep)
        first.acquire()
        second = FileLock(path, timeout=0.3, clock=clock, sleep=sleep)
        with pytest.raises(LockTimeout) as excinfo:
            second.acquire()
        assert excinfo.value.code == "E_AUTH_LOCKED"
        first.release()
        second.acquire()
        assert os.path.isdir(path)
        second.release()

    def test_stale_lock_is_broken(self, tmp_path) -> None:
        path = str(tmp_path / "auth.lock")
        os.mkdir(path)  # 模拟"上一个进程崩溃,锁留在磁盘上"
        (tmp_path / "auth.lock" / "owner").write_text("pid=999999\n", encoding="utf-8")
        # 陈旧判定读的是**目录 mtime** (与真实时钟比较), 所以直接把 mtime 拨老 31s.
        old = time.time() - 31
        os.utime(path, (old, old))
        lock = FileLock(path, timeout=1)
        lock.acquire()
        assert os.path.isdir(path)
        lock.release()
        assert not os.path.exists(path)

    def test_fresh_lock_is_not_broken(self, tmp_path) -> None:
        path = str(tmp_path / "auth.lock")
        os.mkdir(path)
        clock, _advance, sleep = _clock_of()
        with pytest.raises(LockTimeout):
            FileLock(path, timeout=0.2, clock=clock, sleep=sleep).acquire()
        assert os.path.isdir(path), "不是陈旧的锁不能被抢"

    def test_lock_path_is_next_to_auth_json(self, tmp_path) -> None:
        store = AuthStore(state_dir=str(tmp_path), fs=FileStore())
        assert store.lock().path == lock_path(str(tmp_path))
        assert store.lock().path.endswith("auth.lock")
        assert os.path.dirname(store.path) == os.path.dirname(store.lock().path)


class TestRefreshHoldsLock:
    """`Authenticator.ensure()` 必须在锁内**重读**,别人刚刷完就不要再刷。"""

    def test_state_dir_lock_blocks_concurrent_refresh(self, tmp_path, monkeypatch) -> None:
        from orzmc.domain.auth import AuthSession, MsaTokens
        from orzmc.services import auth as auth_service

        store = AuthStore(state_dir=str(tmp_path), fs=FileStore())
        old = AuthSession(
            uuid="u",
            name="n",
            access_token="mc",
            expires_at=100,  # 已过期 -> 需要刷新
            msa=MsaTokens(
                access_token="msa",
                refresh_token="refresh",
                expires_at=100,
                token_type="Bearer",
                scope="XboxLive.signin",
            ),
        )
        store.save(old, client_id="cid")

        refreshed: list[str] = []

        class FakeHttp:
            def get_json(self, *_args, **_kwargs):
                return {}

        def fake_refresh(self, msa):
            refreshed.append(msa.refresh_token)
            return AuthSession(
                uuid="u",
                name="n",
                access_token="new",
                expires_at=10_000,
                msa=MsaTokens(
                    access_token="msa2",
                    refresh_token="refresh2",
                    expires_at=10_000,
                    token_type="Bearer",
                    scope="XboxLive.signin",
                ),
            )

        monkeypatch.setattr(auth_service.Authenticator, "refresh", fake_refresh)
        authenticator = auth_service.Authenticator(
            http=FakeHttp(),  # type: ignore[arg-type]
            store=store,
            client_id="cid",
            clock=lambda: 500.0,
        )

        # 模拟"另一个前端刚刷完": 持锁期间把文件改成有效会话.
        lock = store.lock()
        lock.acquire()
        try:
            store.save(
                AuthSession(
                    uuid="u",
                    name="n",
                    access_token="other",
                    expires_at=10_000,
                    msa=MsaTokens(
                        access_token="msa3",
                        refresh_token="refresh3",
                        expires_at=10_000,
                        token_type="Bearer",
                        scope="XboxLive.signin",
                    ),
                ),
                client_id="cid",
            )
        finally:
            lock.release()

        session = authenticator.ensure()

        assert session.access_token == "other", "应复用另一个前端刚写好的会话"
        assert refreshed == [], "锁内重读发现已有效,不应再刷新(否则 refresh token 会被轮换掉)"

    def test_lock_timeout_surfaces_as_auth_locked(self, tmp_path, monkeypatch) -> None:
        from orzmc.domain.auth import AuthSession, MsaTokens
        from orzmc.services import auth as auth_service

        store = AuthStore(state_dir=str(tmp_path), fs=FileStore())
        store.save(
            AuthSession(
                uuid="u",
                name="n",
                access_token="mc",
                expires_at=100,
                msa=MsaTokens(
                    access_token="msa",
                    refresh_token="refresh",
                    expires_at=100,
                    token_type="Bearer",
                    scope="XboxLive.signin",
                ),
            ),
            client_id="cid",
        )
        # 另一个进程持锁不放 (未过期)
        held = store.lock()
        held.acquire()
        try:
            authenticator = auth_service.Authenticator(
                http=object(),  # type: ignore[arg-type]
                store=AuthStore(state_dir=str(tmp_path), fs=FileStore()),
                client_id="cid",
                clock=lambda: 500.0,
            )
            monkeypatch.setattr(
                auth_store_module,
                "DEFAULT_TIMEOUT",
                0.05,
                raising=False,
            )
            with pytest.raises(LockTimeout) as excinfo:
                authenticator.ensure()
            assert excinfo.value.code == "E_AUTH_LOCKED"
        finally:
            held.release()
