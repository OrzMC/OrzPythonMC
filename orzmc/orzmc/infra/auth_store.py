"""认证令牌持久化:``<状态目录>/auth.json``(原子写 + 0600)。

契约见父仓库 ``docs/spec/auth.md`` §5:令牌放在 OrzMC 状态目录,不出现在
游戏根目录,Unix 文件 ``0600``,写入原子化,绝不记录 token 到日志。
"""

from __future__ import annotations

import json
import os

from orzmc.domain.auth import AuthSession
from orzmc.infra.fs import FileStore, default_state_dir

FILENAME = "auth.json"
PRIVATE_MODE = 0o600


class AuthStore:
    """读写当前用户的认证会话(无会话 / 损坏 / 过期都由调用方处理)。"""

    def __init__(self, state_dir: str | None = None, fs: FileStore | None = None) -> None:
        self._fs = fs or FileStore()
        self._path = os.path.join(state_dir or default_state_dir(), FILENAME)

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> AuthSession | None:
        """Return the stored session, or ``None`` when absent/corrupt."""
        if not self._fs.is_file(self._path):
            return None
        try:
            data = self._fs.read_json(self._path)
        except (OSError, ValueError):
            return None
        try:
            return AuthSession.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, session: AuthSession, client_id: str) -> None:
        payload = json.dumps(session.to_dict(client_id), ensure_ascii=False, indent=2)
        self._fs.write_text_atomic(self._path, payload, mode=PRIVATE_MODE)

    def clear(self) -> None:
        self._fs.remove(self._path)
