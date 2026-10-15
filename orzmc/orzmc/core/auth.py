"""Microsoft / Xbox Live 设备码认证的**纯映射**(请求构造,响应解析,错误分类)。

语义与父仓库 ``docs/spec/auth.md`` 一致,并被一致性样例 ``auth/*`` 锁定。
本模块**不做任何 IO**(HTTP 发送,轮询,令牌持久化分别在 ``infra`` / ``services``)。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from orzmc.domain.auth import (
    DeviceCode,
    MinecraftToken,
    MsaTokens,
    Profile,
    TokenErrorAction,
    TokenState,
    XboxToken,
)

# ── 端点与常量(规范值,见 docs/spec/auth.md §3)─────────────────────────────
AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
DEFAULT_SCOPE = "XboxLive.signin offline_access"
# 内置 Azure 公共客户端 client_id(组织目录 + 个人账号,Allow public client flows = Yes)。
# 空串 = 未内置;可用环境变量 ORZMC_AZURE_CLIENT_ID 覆盖(见 docs/spec/auth.md §10)。
DEFAULT_CLIENT_ID = "1a3e628b-c9a5-4a7f-b7b7-562e22b9787e"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
XBL_RELYING_PARTY = "http://auth.xboxlive.com"
XSTS_RELYING_PARTY = "rp://api.minecraftservices.com/"
AUTH_SKEW_SECONDS = 60
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

# XSTS 已知拒绝码(无 Xbox 账号 / 地区不可用 / 未成年)
_XSTS_KNOWN_ERRORS = {2148916233, 2148916235, 2148916238}


@dataclass(frozen=True)
class HttpRequest:
    """纯数据请求描述;由 services 层翻译成实际 HTTP 调用。"""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] | None = None
    body: dict[str, Any] | None = None


def _dashed_uuid(hex32: str) -> str:
    """32 位无连字符 UUID → 规范 8-4-4-4-12(非 32 位则原样返回)。"""
    h = (hex32 or "").replace("-", "").lower()
    if len(h) != 32:
        return hex32
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _xui_uhs(data: Mapping[str, Any]) -> str:
    xui = (data.get("DisplayClaims") or {}).get("xui") or [{}]
    return str((xui[0] or {}).get("uhs") or "")


# ── ① 设备码 ─────────────────────────────────────────────────────────────────
def device_code_request(client_id: str, scope: str = DEFAULT_SCOPE) -> HttpRequest:
    return HttpRequest("POST", f"{AUTHORITY}/devicecode", form={"client_id": client_id, "scope": scope})


def device_code_result(data: Mapping[str, Any]) -> DeviceCode:
    return DeviceCode(
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data["verification_uri"]),
        expires_in=int(data["expires_in"]),
        interval=int(data.get("interval") or 5),
        message=data.get("message"),
    )


# ── ② 令牌轮询 / 刷新 ────────────────────────────────────────────────────────
def token_poll_request(client_id: str, device_code: str) -> HttpRequest:
    return HttpRequest(
        "POST",
        f"{AUTHORITY}/token",
        form={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": device_code,
        },
    )


def token_refresh_request(client_id: str, refresh_token: str, scope: str = DEFAULT_SCOPE) -> HttpRequest:
    return HttpRequest(
        "POST",
        f"{AUTHORITY}/token",
        form={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": scope,
        },
    )


def token_result(data: Mapping[str, Any], now: float) -> MsaTokens:
    return MsaTokens(
        access_token=str(data["access_token"]),
        refresh_token=str(data["refresh_token"]),
        expires_at=now + int(data["expires_in"]),
        token_type=str(data.get("token_type") or "Bearer"),
        scope=data.get("scope"),
    )


# ── ③ Xbox Live(RPS)────────────────────────────────────────────────────────
def xbox_request(msa_access_token: str) -> HttpRequest:
    return HttpRequest(
        "POST",
        XBL_URL,
        headers=dict(JSON_HEADERS),
        body={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": "d=" + msa_access_token,
            },
            "RelyingParty": XBL_RELYING_PARTY,
            "TokenType": "JWT",
        },
    )


def xbox_result(data: Mapping[str, Any]) -> XboxToken:
    return XboxToken(token=str(data["Token"]), user_hash=_xui_uhs(data))


# ── ④ XSTS ──────────────────────────────────────────────────────────────────
def xsts_request(xbl_token: str, relying_party: str = XSTS_RELYING_PARTY) -> HttpRequest:
    return HttpRequest(
        "POST",
        XSTS_URL,
        headers=dict(JSON_HEADERS),
        body={
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": relying_party,
            "TokenType": "JWT",
        },
    )


def xsts_result(data: Mapping[str, Any]) -> XboxToken:
    return XboxToken(token=str(data["Token"]), user_hash=_xui_uhs(data))


# ── ⑤ Minecraft 登录 ────────────────────────────────────────────────────────
def minecraft_login_request(user_hash: str, xsts_token: str) -> HttpRequest:
    return HttpRequest(
        "POST",
        MC_LOGIN_URL,
        headers=dict(JSON_HEADERS),
        body={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
    )


def minecraft_login_result(data: Mapping[str, Any]) -> MinecraftToken:
    return MinecraftToken(access_token=str(data["access_token"]), expires_in=int(data["expires_in"]))


# ── ⑥ 档案 ──────────────────────────────────────────────────────────────────
def profile_request(access_token: str) -> HttpRequest:
    return HttpRequest("GET", MC_PROFILE_URL, headers={"Authorization": "Bearer " + access_token})


def profile_result(data: Mapping[str, Any]) -> Profile:
    return Profile(uuid=_dashed_uuid(str(data.get("id") or "")), name=str(data.get("name") or ""))


# ── 令牌状态与错误分类 ───────────────────────────────────────────────────────
def token_state(expires_at: float, now: float, skew: int = AUTH_SKEW_SECONDS) -> TokenState:
    remaining = int(expires_at - now)
    if remaining <= 0:
        state = "expired"
    elif remaining <= skew:
        state = "stale"
    else:
        state = "valid"
    return TokenState(state=state, remaining_seconds=remaining)


def classify_token_error(error: str | None) -> TokenErrorAction:
    action, code = {
        "authorization_pending": ("wait", None),
        "slow_down": ("slowDown", None),
        "expired_token": ("fail", "E_AUTH_EXPIRED"),
        "access_denied": ("fail", "E_AUTH_DENIED"),
        "authorization_declined": ("fail", "E_AUTH_DENIED"),
    }.get(error or "", ("fail", "E_AUTH"))
    return TokenErrorAction(action=action, code=code)


def classify_xsts_error(status: int, xerr: Any = None) -> str | None:
    """XSTS 响应 → 规范错误码;200 表示成功(``None``)。"""
    if status == 200:
        return None
    if xerr is None:
        return "E_AUTH"
    return "E_AUTH_XBOX" if int(xerr) in _XSTS_KNOWN_ERRORS else "E_AUTH"
