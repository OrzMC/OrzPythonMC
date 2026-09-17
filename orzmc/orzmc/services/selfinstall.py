"""Install record + self-uninstall: remove the binary, restore PATH, drop game data.

Backs the ``orzmc self-uninstall`` CLI command. Follows the ``VersionManager`` /
``Backup`` injection pattern: pure filesystem + reporter, no network, no
system-level process management.

The install record (``install.conf``) and the "is this a real install?" guards
shared here are also used by :mod:`orzmc.services.selfupdate` — self-upgrade
has the same notion of *what* was installed and *where*.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from orzmc.domain.paths import DEFAULT_ROOT
from orzmc.infra.fs import FileStore
from orzmc.infra.log import NullReporter, Reporter

MANIFEST_FILENAME = "install.conf"
BINARY_FALLBACK_NAME = ".orzmc-manifest"
SCHEMA = 1


def looks_like_dev(binary: str) -> bool:
    """True when the binary sits in a venv / site-packages / PyInstaller temp dir.

    Guards both self-uninstall and self-update: replacing or deleting a
    developer/pip-managed artifact would break the tool that owns it.
    """
    parts = os.path.abspath(binary).replace(os.sep, "/").split("/")
    for part in parts:
        if part in (".venv", "venv", "env") or part.startswith("_MEI"):
            return True
    lowered = binary.lower()
    if "site-packages" in lowered or "dist-packages" in lowered:
        return True
    mei = getattr(sys, "_MEIPASS", None)
    return isinstance(mei, str) and os.path.dirname(os.path.abspath(mei)) == os.path.dirname(binary)


def is_pip_managed(binary: str) -> bool:
    """True when pip/pipx owns the binary, so upgrading must go through it."""
    lowered = binary.lower()
    if "site-packages" in lowered or "dist-packages" in lowered:
        return True
    directory = os.path.dirname(binary)
    if not os.path.isdir(directory):
        return False
    try:
        entries = os.listdir(directory)
    except OSError:
        return False
    return any(name.endswith(".dist-info") or name.endswith(".egg-info") for name in entries)


def schedule_delete_on_reboot(path: str) -> bool:
    """Ask Windows to delete a locked file at next boot; False elsewhere/on failure."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        return bool(cast(Any, ctypes).windll.kernel32.MoveFileExW(path, None, MOVEFILE_DELAY_UNTIL_REBOOT))
    except Exception:
        return False


def default_state_dir() -> str:
    """Per-user state dir for the install manifest (platform-aware).

    Honors ``XDG_STATE_HOME`` (Unix) / ``LOCALAPPDATA`` (Windows) so the
    installer and ``self-uninstall`` agree on where the manifest lives, and so
    CI can redirect state into a temp dir for a hermetic install→uninstall loop.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "orzmc")


@dataclass(frozen=True)
class InstallManifest:
    """Install record written by the one-line installer (key=value lines).

    Optional fields (``source`` / ``path_file`` / ``path_line``) are omitted
    from the file when ``None``; values are everything after the first ``=``.
    """

    tool: str = "orzmc"
    schema: int = SCHEMA
    version: str = ""
    platform: str = ""
    install_dir: str = ""
    binary: str = ""
    source: str | None = None
    path_file: str | None = None
    path_line: str | None = None
    root_dir: str = DEFAULT_ROOT

    # ── serialization ──────────────────────────────────────────────────────

    def to_lines(self) -> list[str]:
        lines = [
            f"tool={self.tool}",
            f"schema={self.schema}",
            f"version={self.version}",
            f"platform={self.platform}",
            f"install_dir={self.install_dir}",
            f"binary={self.binary}",
        ]
        if self.source is not None:
            lines.append(f"source={self.source}")
        if self.path_file is not None:
            lines.append(f"path_file={self.path_file}")
        if self.path_line is not None:
            lines.append(f"path_line={self.path_line}")
        lines.append(f"root_dir={self.root_dir}")
        return lines

    @classmethod
    def from_lines(cls, lines: list[str]) -> InstallManifest:
        fields: dict[str, str] = {}
        for line in lines:
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            fields[key.strip()] = value
        try:
            schema = int(fields.get("schema", SCHEMA))
        except (TypeError, ValueError):
            schema = SCHEMA
        return cls(
            tool=fields.get("tool", "orzmc"),
            schema=schema,
            version=fields.get("version", ""),
            platform=fields.get("platform", ""),
            install_dir=fields.get("install_dir", ""),
            binary=fields.get("binary", ""),
            source=fields.get("source"),
            path_file=fields.get("path_file"),
            path_line=fields.get("path_line"),
            root_dir=fields.get("root_dir", DEFAULT_ROOT),
        )

    # ── persistence ────────────────────────────────────────────────────────

    def write(self, fs: FileStore, path: str | None = None) -> str:
        """Write the manifest (key=value lines); returns the path written."""
        path = path or os.path.join(default_state_dir(), MANIFEST_FILENAME)
        fs.write_text(path, "\n".join(self.to_lines()) + "\n", encoding="utf-8")
        return path

    @classmethod
    def read(cls, fs: FileStore, path: str) -> InstallManifest:
        # utf-8-sig: PowerShell 5.1 Set-Content -Encoding UTF8 writes a BOM.
        return cls.from_lines(fs.read_text(path, encoding="utf-8-sig").splitlines())

    @classmethod
    def find(cls, fs: FileStore, binary: str | None = None) -> tuple[str, InstallManifest] | None:
        """Locate the install record: state dir first, then next to the binary."""
        state = os.path.join(default_state_dir(), MANIFEST_FILENAME)
        if fs.is_file(state):
            return state, cls.read(fs, state)
        if binary:
            alt = os.path.join(os.path.dirname(binary), BINARY_FALLBACK_NAME)
            if fs.is_file(alt):
                return alt, cls.read(fs, alt)
        return None


class SelfUninstaller:
    def __init__(
        self, fs: FileStore | None = None, reporter: Reporter | None = None, root_dir: str | None = None
    ) -> None:
        self._fs = fs or FileStore()
        self._reporter = reporter or NullReporter()
        self._root_override = root_dir

    def uninstall(
        self,
        binary: str,
        *,
        remove_root: bool = False,
        yes: bool = False,
        force: bool = False,
        confirm: Callable[[str], bool] | None = None,
    ) -> bool:
        """Remove the tool binary and undo its PATH/state side effects.

        ``binary`` is the running executable (``sys.argv[0]``). ``confirm`` is
        consulted for game-data removal unless ``remove_root`` (delete without
        asking) or ``yes`` (default to keep) applies. Returns True when the
        binary was removed.
        """
        binary = os.path.abspath(os.path.expanduser(binary))
        if not force and looks_like_dev(binary):
            raise RuntimeError(
                "检测到疑似开发环境安装(venv / site-packages / PyInstaller 临时目录),已拒绝卸载;"
                "确认无误可加 --force 继续"
            )

        found = InstallManifest.find(self._fs, binary=binary)
        root = self._root_override or (found[1].root_dir if found else DEFAULT_ROOT)

        if found is None:
            if is_pip_managed(binary):
                self._reporter.warn("检测到 pip 安装的 orzmc-app,请用 pip uninstall orzmc-app 卸载")
                self._handle_root(root, remove_root=remove_root, yes=yes, confirm=confirm)
                return False
            self._reporter.warn("未找到安装记录,已尽力清理")
            self._handle_root(root, remove_root=remove_root, yes=yes, confirm=confirm)
            self._remove_binary(binary)
            self._prune_empty(os.path.dirname(binary))
            return True

        manifest_path, manifest = found
        if self._restore_path(manifest):
            self._reporter.info("已还原 PATH 配置")
        self._fs.remove(manifest_path)
        self._prune_empty(os.path.dirname(manifest_path))
        self._handle_root(root, remove_root=remove_root, yes=yes, confirm=confirm)
        self._reporter.success("已卸载 orzmc")
        # 删除自身二进制放最后:PyInstaller onefile 删除运行中 exe 后,
        # 任何按需模块加载都会因读归档失败而 SystemExit。
        self._remove_binary(manifest.binary or binary)
        self._prune_empty(manifest.install_dir)
        return True

    # ── internals ──────────────────────────────────────────────────────────

    def _restore_path(self, manifest: InstallManifest) -> bool:
        # rc 文件式安装(install.sh 记录 path_file+path_line)在 Windows 上
        # 也先还原 rc 文件;注册表还原只用于无 path_file 的安装(install.ps1
        # 只记 path_line=install_dir token)。
        if manifest.path_file and manifest.path_line:
            return self._remove_line_from_file(manifest.path_file, manifest.path_line)
        if os.name == "nt" and manifest.path_line:
            return self._restore_windows_path(manifest)
        return False

    def _remove_line_from_file(self, path: str, line: str) -> bool:
        """Remove the exact recorded line from a shell rc file (never a regex)."""
        if not self._fs.is_file(path):
            self._reporter.info("PATH 配置文件不存在,跳过还原")
            return False
        content = self._fs.read_text(path, encoding="utf-8-sig")
        lines = content.split("\n")
        if line not in lines:
            self._reporter.warn("未在 rc 文件中找到记录的 PATH 行(可能已被手动修改),跳过还原")
            return False
        kept = [ln for ln in lines if ln != line]
        joined = "\n".join(kept)
        if content.endswith("\n"):
            joined += "\n"
        self._fs.write_text(path, joined, encoding="utf-8")
        return True

    def _restore_windows_path(self, manifest: InstallManifest) -> bool:
        """Remove the recorded install dir from the User PATH; failures only warn.

        Only runs when ``path_line`` was recorded (install.ps1 always writes
        it); a manifest without one never touches the registry.
        """
        token = manifest.path_line
        if not token:
            return False
        try:
            import winreg

            reg = cast(Any, winreg)
            key = reg.OpenKey(reg.HKEY_CURRENT_USER, "Environment", 0, reg.KEY_QUERY_VALUE | reg.KEY_SET_VALUE)
            try:
                value, _ = reg.QueryValueEx(key, "Path")
                parts = [p for p in value.split(";") if p]
                if token not in parts:
                    return False
                reg.SetValueEx(key, "Path", 0, reg.REG_EXPAND_SZ, ";".join(p for p in parts if p != token))
            finally:
                reg.CloseKey(key)
            self._broadcast_env_change()
            return True
        except Exception:
            self._reporter.warn("无法修改 Windows 用户 PATH,请手动移除安装目录")
            return False

    @staticmethod
    def _broadcast_env_change() -> None:
        try:
            import ctypes

            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            cast(Any, ctypes).windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
            )
        except Exception:
            pass

    def _remove_binary(self, binary: str) -> None:
        try:
            self._fs.remove(binary)
            return
        except OSError:
            pass
        if os.name != "nt":
            raise
        # A running Windows exe may be locked: rename→delete, else schedule reboot-delete.
        try:
            os.rename(binary, binary + ".old")
            self._fs.remove(binary + ".old")
            return
        except OSError:
            pass
        if schedule_delete_on_reboot(binary):
            self._reporter.warn("二进制被占用,已标记重启后删除")
            return
        self._reporter.warn(f"无法删除二进制:{binary},请手动删除")

    def _handle_root(
        self,
        root: str,
        *,
        remove_root: bool,
        yes: bool,
        confirm: Callable[[str], bool] | None,
    ) -> None:
        if remove_root:
            self._fs.remove(root)
            self._reporter.success(f"已删除游戏数据:{root}")
        elif yes:
            self._reporter.info(f"游戏数据保留:{root}")
        elif confirm is not None and confirm(f"是否同时删除游戏数据 {root}?"):
            self._fs.remove(root)
            self._reporter.success(f"已删除游戏数据:{root}")
        else:
            self._reporter.info(f"游戏数据保留:{root}")

    @staticmethod
    def _prune_empty(path: str) -> None:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
