"""Auth: pure mapping, token store, device-code orchestration (no network)."""

from __future__ import annotations

import json as _json
import os
import stat

import pytest
from fakes import FakeHttp, FakeResponse

from orzmc.core import auth as auth_map
from orzmc.domain.auth import AuthSession, MsaTokens
from orzmc.domain.launch import game_args
from orzmc.domain.options import RuntimeOptions
from orzmc.domain.paths import PathLayout
from orzmc.infra.auth_store import AuthStore
from orzmc.services.auth import Authenticator, AuthError

_UUID_UNDASHED = "069a79f444e94726a5befca90e38aaf5"
_UUID_DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


# ── 纯映射 ────────────────────────────────────────────────────────────────────
def test_device_code_request_uses_consumers_and_scope() -> None:
    req = auth_map.device_code_request("cid")
    assert req.method == "POST"
    assert req.url == "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
    assert req.form == {"client_id": "cid", "scope": "XboxLive.signin offline_access"}


def test_xbox_request_prefixes_rps_ticket_with_d() -> None:
    req = auth_map.xbox_request("MSA")
    assert req.body is not None
    assert req.body["Properties"]["RpsTicket"] == "d=MSA"
    assert req.body["RelyingParty"] == "http://auth.xboxlive.com"


def test_profile_result_normalizes_uuid() -> None:
    profile = auth_map.profile_result({"id": _UUID_UNDASHED, "name": "Notch"})
    assert profile.uuid == _UUID_DASHED
    assert profile.name == "Notch"


def test_token_state_boundaries() -> None:
    assert auth_map.token_state(1000, 900).state == "valid"
    assert auth_map.token_state(1000, 960).state == "stale"
    assert auth_map.token_state(1000, 1000).state == "expired"


def test_classify_token_error_actions() -> None:
    assert auth_map.classify_token_error("authorization_pending").action == "wait"
    assert auth_map.classify_token_error("slow_down").action == "slowDown"
    expired = auth_map.classify_token_error("expired_token")
    assert (expired.action, expired.code) == ("fail", "E_AUTH_EXPIRED")
    assert auth_map.classify_token_error("nope").code == "E_AUTH"


def test_classify_xsts_error_known_codes() -> None:
    assert auth_map.classify_xsts_error(401, 2148916233) == "E_AUTH_XBOX"
    assert auth_map.classify_xsts_error(401, 12345) == "E_AUTH"
    assert auth_map.classify_xsts_error(200) is None


# ── 令牌存储 ──────────────────────────────────────────────────────────────────
def _session(expires_at: float = 10_000.0) -> AuthSession:
    return AuthSession(
        uuid=_UUID_DASHED,
        name="Notch",
        access_token="MC",
        expires_at=expires_at,
        msa=MsaTokens(access_token="MSA", refresh_token="REFRESH", expires_at=expires_at),
    )


def test_auth_store_roundtrip_is_private_and_atomic(tmp_path) -> None:
    store = AuthStore(str(tmp_path))
    assert store.load() is None
    store.save(_session(), "cid")
    assert os.path.isfile(store.path)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    # 原子写:目录里不留临时文件。
    assert not [name for name in os.listdir(tmp_path) if name.startswith(".tmp-")]
    loaded = store.load()
    assert loaded is not None
    assert loaded.uuid == _UUID_DASHED
    assert loaded.msa.refresh_token == "REFRESH"
    store.clear()
    assert store.load() is None


def test_auth_store_ignores_corrupt_file(tmp_path) -> None:
    store = AuthStore(str(tmp_path))
    with open(store.path, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert store.load() is None


# ── 设备码编排 ────────────────────────────────────────────────────────────────
class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _device_flow_http() -> FakeHttp:
    http = FakeHttp()
    http.api_responses["/devicecode"] = (
        200,
        {
            "device_code": "DEV",
            "user_code": "ABCD",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        },
    )
    http.api_responses["oauth2/v2.0/token"] = (
        200,
        {"access_token": "MSA", "refresh_token": "REFRESH", "expires_in": 3600},
    )
    http.api_responses["user.auth.xboxlive.com"] = (
        200,
        {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}},
    )
    http.api_responses["xsts.auth.xboxlive.com"] = (
        200,
        {"Token": "XSTS", "DisplayClaims": {"xui": [{"uhs": "UH"}]}},
    )
    http.api_responses["login_with_xbox"] = (200, {"access_token": "MC", "expires_in": 3600})
    http.api_responses["/minecraft/profile"] = (200, {"id": _UUID_UNDASHED, "name": "Notch"})
    return http


def test_ensure_runs_device_code_then_reuses_cache(tmp_path) -> None:
    http = _device_flow_http()
    shown: list[object] = []
    auth = Authenticator(
        http, AuthStore(str(tmp_path)), "cid", on_device_code=shown.append, clock=_Clock(), sleep=lambda _s: None
    )
    session = auth.ensure()
    assert session.source == "device_code"
    assert session.name == "Notch"
    assert session.uuid == _UUID_DASHED
    assert session.msa.refresh_token == "REFRESH"
    assert shown and getattr(shown[0], "user_code", None) == "ABCD"
    # 第二次:命中缓存,不再发任何请求。
    before = len(http.api_requests)
    again = auth.ensure()
    assert again.source == "cache"
    assert len(http.api_requests) == before


def test_ensure_refreshes_stale_session(tmp_path) -> None:
    http = _device_flow_http()
    store = AuthStore(str(tmp_path))
    store.save(_session(expires_at=1040.0), "cid")  # now=1000 → 剩余 40s,进入刷新窗口。
    auth = Authenticator(http, store, "cid", clock=_Clock(1000.0), sleep=lambda _s: None)
    session = auth.ensure()
    assert session.source == "refresh"
    token_calls = [r for r in http.api_requests if r["url"].endswith("/token")]
    assert token_calls and token_calls[0]["data"]["grant_type"] == "refresh_token"


def test_xsts_rejection_maps_to_e_auth_xbox(tmp_path) -> None:
    http = _device_flow_http()
    http.api_responses["xsts.auth.xboxlive.com"] = (401, {"XErr": 2148916233, "Message": "无 Xbox 账号"})
    auth = Authenticator(http, AuthStore(str(tmp_path)), "cid", clock=_Clock(), sleep=lambda _s: None)
    with pytest.raises(AuthError) as excinfo:
        auth.ensure()
    assert excinfo.value.code == "E_AUTH_XBOX"


def test_profile_401_maps_to_no_game(tmp_path) -> None:
    http = _device_flow_http()
    http.api_responses["/minecraft/profile"] = (401, {"error": "Unauthorized"})
    auth = Authenticator(http, AuthStore(str(tmp_path)), "cid", clock=_Clock(), sleep=lambda _s: None)
    with pytest.raises(AuthError) as excinfo:
        auth.ensure()
    assert excinfo.value.code == "E_AUTH_NO_GAME"


class _ScriptedHttp(FakeHttp):
    """按顺序返回预置响应(用于覆盖轮询:pending → 成功)。"""

    def __init__(self, script: list[tuple[int, dict]]) -> None:
        super().__init__()
        self._script = list(script)

    def request(self, method, url, *, params=None, data=None, json=None, headers=None, timeout=None):
        self.api_requests.append({"method": method, "url": url, "data": data})
        status, payload = self._script.pop(0)
        return FakeResponse(status, _json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})


def test_poll_waits_then_succeeds(tmp_path) -> None:
    http = _ScriptedHttp(
        [
            (
                200,
                {
                    "device_code": "DEV",
                    "user_code": "ABCD",
                    "verification_uri": "https://x",
                    "expires_in": 900,
                    "interval": 1,
                },
            ),
            (400, {"error": "authorization_pending"}),
            (200, {"access_token": "MSA", "refresh_token": "REFRESH", "expires_in": 3600}),
            (200, {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}}),
            (200, {"Token": "XSTS", "DisplayClaims": {"xui": [{"uhs": "UH"}]}}),
            (200, {"access_token": "MC", "expires_in": 3600}),
            (200, {"id": _UUID_UNDASHED, "name": "Notch"}),
        ]
    )
    auth = Authenticator(http, AuthStore(str(tmp_path)), "cid", clock=_Clock(), sleep=lambda _s: None)
    session = auth.ensure()
    assert session.name == "Notch"
    # 第二次 token 调用(成功)确实发生在 pending 之后。
    assert [r["url"].split("/")[-1] for r in http.api_requests].count("token") == 2


# ── 启动参数接线 ──────────────────────────────────────────────────────────────
def test_launch_args_use_authenticated_session() -> None:
    version_json = {
        "minecraftArguments": (
            "--username ${auth_player_name} --uuid ${auth_uuid} "
            "--accessToken ${auth_access_token} --userType ${user_type}"
        )
    }
    options = RuntimeOptions(
        username="Notch",
        auth_uuid=_UUID_DASHED,
        auth_access_token="MC",
        auth_user_type="msa",
    )
    args = game_args(version_json, options, PathLayout(root="/tmp/x", version="1.20.4"))
    assert "--username" in args and args[args.index("--username") + 1] == "Notch"
    assert args[args.index("--accessToken") + 1] == "MC"
    assert args[args.index("--userType") + 1] == "msa"
    assert args[args.index("--uuid") + 1] == _UUID_DASHED


def test_offline_launch_args_stay_legacy() -> None:
    version_json = {"minecraftArguments": "--accessToken ${auth_access_token} --userType ${user_type}"}
    args = game_args(version_json, RuntimeOptions(username="guest"), PathLayout(root="/tmp/x", version="1.20.4"))
    assert args[args.index("--accessToken") + 1] == "0"
    assert args[args.index("--userType") + 1] == "legacy"
