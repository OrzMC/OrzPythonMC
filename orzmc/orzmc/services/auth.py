"""正版授权编排(设备码流程 + 轮询 + 令牌持久化)。

纯映射在 :mod:`orzmc.core.auth`,本模块负责 IO:发请求,按 ``interval`` 轮询,
刷新,把会话写入 :class:`~orzmc.infra.auth_store.AuthStore`。契约见
父仓库 ``docs/spec/auth.md``;任何日志都不得包含 token。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from orzmc.core import auth as auth_map
from orzmc.domain.auth import AuthSession, DeviceCode, MsaTokens
from orzmc.infra.auth_store import AuthStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter


class AuthError(RuntimeError):
    """认证失败,携带规范错误码(``E_AUTH*``,见 docs/spec/auth.md §7)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Authenticator:
    """设备码认证:缓存复用 → 刷新 → 重新登录。"""

    def __init__(
        self,
        http: HttpClient,
        store: AuthStore,
        client_id: str,
        *,
        reporter: Reporter | None = None,
        on_device_code: Callable[[DeviceCode], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._store = store
        self._client_id = client_id
        self._reporter = reporter or NullReporter()
        self._on_device_code = on_device_code
        self._clock = clock
        self._sleep = sleep

    # ── 对外入口 ────────────────────────────────────────────────────────────
    def ensure(self) -> AuthSession:
        """返回一个 MC 令牌有效的会话:缓存 → 刷新 → 设备码登录。

        **刷新必须持跨进程锁, 并在锁内先重读** (`frontends.md` F4/F4.1):
        刷新会轮换 refresh token,两个前端同时刷会让后者的令牌立刻失效(用户被登出)。
        """
        session = self._store.load()
        if session is not None and auth_map.token_state(session.expires_at, self._clock()).state == "valid":
            return replace(session, source="cache")
        if session is not None:
            with self._store.lock():
                # 锁内**重读**:另一个前端可能刚刷完,直接用它的结果,别再刷一次。
                current = self._store.load()
                if current is not None and auth_map.token_state(current.expires_at, self._clock()).state == "valid":
                    return replace(current, source="cache")
                target = current if current is not None else session
                try:
                    return self.refresh(target.msa)
                except AuthError:
                    # refresh token 被拒(如用户改密/撤销授权)→ 清掉重登,不吞错。
                    self._store.clear()
        return self.login()

    def login(self) -> AuthSession:
        """完整设备码流程(用户需在浏览器完成授权)。"""
        status, data = self._send(auth_map.device_code_request(self._client_id))
        if status != 200:
            raise AuthError("E_AUTH", _error_text(data))
        device = auth_map.device_code_result(data)
        if self._on_device_code is not None:
            self._on_device_code(device)
        msa = self._poll(device)
        return self._complete(msa, source="device_code")

    def refresh(self, msa: MsaTokens) -> AuthSession:
        """用 MSA ``refresh_token`` 换新 MSA 令牌,再重走 Xbox 链路。"""
        status, data = self._send(auth_map.token_refresh_request(self._client_id, msa.refresh_token))
        if status != 200:
            action = auth_map.classify_token_error(data.get("error"))
            raise AuthError(action.code or "E_AUTH_EXPIRED", _error_text(data))
        return self._complete(auth_map.token_result(data, self._clock()), source="refresh")

    # ── 内部 ────────────────────────────────────────────────────────────────
    def _poll(self, device: DeviceCode) -> MsaTokens:
        deadline = self._clock() + device.expires_in
        interval = max(1, device.interval)
        while True:
            status, data = self._send(auth_map.token_poll_request(self._client_id, device.device_code))
            if status == 200:
                return auth_map.token_result(data, self._clock())
            action = auth_map.classify_token_error(data.get("error"))
            if action.action == "slowDown":
                interval += 5
            elif action.action == "fail":
                raise AuthError(action.code or "E_AUTH", _error_text(data))
            if self._clock() + interval > deadline:
                raise AuthError("E_AUTH_EXPIRED", "设备码已过期,请重新运行以再次授权")
            self._sleep(interval)

    def _complete(self, msa: MsaTokens, *, source: str) -> AuthSession:
        status, data = self._send(auth_map.xbox_request(msa.access_token))
        if status != 200:
            raise AuthError("E_AUTH", _error_text(data))
        xbl = auth_map.xbox_result(data)

        status, data = self._send(auth_map.xsts_request(xbl.token))
        if status != 200:
            code = auth_map.classify_xsts_error(status, data.get("XErr")) or "E_AUTH"
            raise AuthError(code, _error_text(data) or "Xbox Live 授权被拒绝")
        xsts = auth_map.xsts_result(data)

        status, data = self._send(auth_map.minecraft_login_request(xsts.user_hash, xsts.token))
        if status != 200:
            raise AuthError("E_AUTH", _error_text(data))
        mc = auth_map.minecraft_login_result(data)

        status, data = self._send(auth_map.profile_request(mc.access_token))
        if status == 401:
            raise AuthError("E_AUTH_NO_GAME", "该账号没有 Minecraft Java 版")
        if status != 200:
            raise AuthError("E_AUTH", _error_text(data))
        profile = auth_map.profile_result(data)

        session = AuthSession(
            uuid=profile.uuid,
            name=profile.name,
            access_token=mc.access_token,
            expires_at=self._clock() + mc.expires_in,
            msa=msa,
            source=source,
        )
        self._store.save(session, self._client_id)
        self._reporter.info(f"正版授权成功:{session.name}")
        return session

    def _send(self, request: auth_map.HttpRequest) -> tuple[int, Mapping[str, Any]]:
        resp = self._http.request(
            request.method,
            request.url,
            data=request.form,
            json=request.body,
            headers=request.headers or None,
        )
        status = int(getattr(resp, "status_code", 200))
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}


def _error_text(data: Mapping[str, Any]) -> str:
    """Human-readable upstream message (never a token)."""
    return str(data.get("error_description") or data.get("Message") or data.get("error") or "认证失败")
