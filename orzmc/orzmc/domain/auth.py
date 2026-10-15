"""认证数据形状(纯数据,无 IO)。

正版授权的语言无关契约见父仓库 ``docs/spec/auth.md``;本模块只定义
设备码会话,令牌状态与错误分类的**数据形状**,供 core / infra / services 共享。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeviceCode:
    """OAuth 设备码响应(用户按 ``verification_uri`` + ``user_code`` 完成授权)。"""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = 5
    message: str | None = None


@dataclass(frozen=True)
class XboxToken:
    """Xbox Live / XSTS 令牌(``user_hash`` 即 ``DisplayClaims.xui[0].uhs``)。"""

    token: str
    user_hash: str


@dataclass(frozen=True)
class MinecraftToken:
    access_token: str
    expires_in: int


@dataclass(frozen=True)
class Profile:
    """Minecraft 档案(``uuid`` 已规范化为带连字符形式)。"""

    uuid: str
    name: str


@dataclass(frozen=True)
class TokenState:
    """令牌生命周期:``valid`` / ``stale``(进入刷新窗口)/ ``expired``。"""

    state: str
    remaining_seconds: int


@dataclass(frozen=True)
class TokenErrorAction:
    """令牌端点错误 → 轮询动作(``wait`` / ``slowDown`` / ``fail``)。"""

    action: str
    code: str | None = None


@dataclass(frozen=True)
class MsaTokens:
    """Microsoft 账号令牌(``refresh_token`` 来自 ``offline_access``)。"""

    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "Bearer"
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "expiresAt": self.expires_at,
            "tokenType": self.token_type,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MsaTokens:
        return cls(
            access_token=str(data["accessToken"]),
            refresh_token=str(data["refreshToken"]),
            expires_at=float(data["expiresAt"]),
            token_type=str(data.get("tokenType") or "Bearer"),
            scope=data.get("scope"),
        )


@dataclass(frozen=True)
class AuthSession:
    """一次可用的认证会话:启动客户端所需的 uuid / 用户名 / MC 令牌。"""

    uuid: str
    name: str
    access_token: str
    expires_at: float
    msa: MsaTokens
    source: str = "cache"

    def to_dict(self, client_id: str) -> dict[str, Any]:
        return {
            "schema": 1,
            "clientId": client_id,
            "account": {"uuid": self.uuid, "name": self.name},
            "minecraft": {"accessToken": self.access_token, "expiresAt": self.expires_at},
            "msa": self.msa.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "cache") -> AuthSession:
        account = data.get("account") or {}
        minecraft = data.get("minecraft") or {}
        return cls(
            uuid=str(account["uuid"]),
            name=str(account["name"]),
            access_token=str(minecraft["accessToken"]),
            expires_at=float(minecraft["expiresAt"]),
            msa=MsaTokens.from_dict(data["msa"]),
            source=source,
        )
