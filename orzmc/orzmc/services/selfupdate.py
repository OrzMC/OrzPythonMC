"""Self-update: stage the matching GitHub Release asset, then swap it in.

Sibling of :mod:`orzmc.services.selfinstall`: the one-line installer writes
``install.conf``, this module reads it (install dir / source) and rewrites the
record after an upgrade.

**The swap is delegated to a detached helper** that waits for this process to
exit. A running PyInstaller onefile binary must never replace its own file:
the PYZ archive is read *lazily* from ``<executable>?<offset>`` (PyInstaller's
``sys._pyinstaller_pyz``), so swapping the bytes underneath a live process
breaks the next not-yet-imported module (``zlib.error: incorrect header
check`` — reproduced on a real release binary). The helper also sidesteps the
Windows rule that a running ``.exe`` cannot be overwritten or deleted, so one
mechanism covers both platforms.

Everything is staged next to the target binary (same volume → the helper's
rename is atomic), and nothing is handed off before its magic bytes prove it is
really an executable — a GitHub HTML error page must never become ``orzmc``.
"""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace

from orzmc.infra.fs import FileStore
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.progress import NullProgress, ProgressSink
from orzmc.infra.runner import ProcessRunner
from orzmc.infra.transfer import download_with_progress
from orzmc.services.selfinstall import InstallManifest, is_pip_managed, looks_like_dev
from orzmc.version import __version__

REPO = "OrzMC/OrzPythonMC"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_BASE = f"https://github.com/{REPO}/releases/download"
USER_AGENT = "orzmc-selfupdate"
STAGING_NAME = ".orzmc-update.tmp"
_LOCAL_VERSION = "local-build"

# Mach-O (thin, both endiannesses, and universal) + ELF. Windows adds MZ.
_EXEC_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
)

_WAIT_TICKS = 300  # helper gives up after ~60s of waiting for us to exit


def applier_command(staged: str, binary: str, pid: int) -> list[str]:
    """Command for the detached helper: wait for ``pid`` to exit, then swap.

    POSIX uses ``/bin/sh`` (always present); Windows uses ``powershell``
    (5.1+, present on every supported release). The rename target is the file
    the helper was given — no Python runs after the swap, so the running
    process never reads from a changed archive.
    """
    if os.name == "nt":
        move = (
            f"try {{ Move-Item -Force -LiteralPath '{_ps_quote(staged)}' "
            f"-Destination '{_ps_quote(binary)}' }} "
            f"catch {{ [System.IO.File]::Copy('{_ps_quote(staged)}', '{_ps_quote(binary)}', $true); "
            f"Remove-Item -LiteralPath '{_ps_quote(staged)}' -Force }}"
        )
        script = (
            f"$i=0; while ((Get-Process -Id {pid} -ErrorAction SilentlyContinue) -and ($i -lt {_WAIT_TICKS})) "
            f"{{ Start-Sleep -Milliseconds 200; $i++ }}; {move}"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    script = (
        f'i=0; while kill -0 "$1" 2>/dev/null && [ "$i" -lt {_WAIT_TICKS} ]; '
        f'do sleep 0.2; i=$((i+1)); done; mv -f "$2" "$3"'
    )
    return ["/bin/sh", "-c", script, "orzmc-update", str(pid), staged, binary]


def api_headers() -> dict[str, str]:
    """Headers for the Releases API, honouring a token when the user has one.

    Unauthenticated calls are limited to 60/hour/IP, which CI, corporate NATs
    and repeated ``--check`` runs can exhaust; ``GITHUB_TOKEN`` / ``GH_TOKEN``
    raises that to 5000/hour. Never required — the ``--version`` fallback works
    offline and without credentials.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _ps_quote(value: str) -> str:
    """Escape a path for a single-quoted PowerShell literal."""
    return value.replace("'", "''")


def asset_for(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    """``(release asset name, platform tag)`` for the running interpreter.

    Mirrors the one-line installers (``orzmc-<os>-<arch>[.exe]``). ``machine``
    is the *process* architecture, which is exactly what must be replaced —
    under Rosetta a x86_64 process upgrades to the x86_64 asset.
    """
    os_name = (system or platform.system()).lower()
    arch = (machine or platform.machine()).lower()
    if os_name == "darwin":
        os_name = "macos"
    elif os_name not in ("linux", "windows"):
        raise RuntimeError(f"暂不支持该系统: {system or platform.system()}")
    if arch in ("arm64", "aarch64"):
        arch = "arm64"
    elif arch in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        raise RuntimeError(f"暂不支持该架构: {machine or platform.machine()}")
    suffix = ".exe" if os_name == "windows" else ""
    return f"orzmc-{os_name}-{arch}{suffix}", f"{os_name}-{arch}"


def normalize_tag(version: str) -> str:
    """``2.1.0`` / ``v2.1.0`` → ``v2.1.0`` (release tags always carry the ``v``)."""
    cleaned = version.strip()
    if not cleaned:
        raise RuntimeError("版本号不能为空")
    return cleaned if cleaned.startswith("v") else f"v{cleaned}"


def version_key(tag: str) -> tuple[int, ...]:
    """Numeric parts of ``vX.Y.Z`` (suffixes ignored); ``()`` when unparsable."""
    numbers: list[int] = []
    for part in tag.strip().lstrip("vV").split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a genuine upgrade over ``current``."""
    if not current or current == _LOCAL_VERSION:
        return True
    if candidate == current:
        return False
    left, right = version_key(candidate), version_key(current)
    if not left or not right:
        return candidate != current
    return left > right


@dataclass(frozen=True)
class UpdateCheck:
    """One resolved upgrade target.

    ``applied`` means the new binary was staged and handed to the detached
    helper (it goes live as soon as this process exits), not that the file was
    already swapped.
    """

    current: str
    latest: str
    asset: str
    platform: str
    url: str | None = None
    available: bool = False
    applied: bool = False


class SelfUpdater:
    """Check for and install a newer ``orzmc`` binary."""

    def __init__(
        self,
        http: HttpClient | None = None,
        fs: FileStore | None = None,
        reporter: Reporter | None = None,
        sink: ProgressSink | None = None,
        process: ProcessRunner | None = None,
        binary: str | None = None,
        asset: tuple[str, str] | None = None,
    ) -> None:
        self._http = http or HttpClient()
        self._fs = fs or FileStore()
        self._reporter = reporter or NullReporter()
        self._sink = sink or NullProgress()
        self._process = process or ProcessRunner(reporter)
        self._binary = os.path.abspath(os.path.expanduser(binary)) if binary else ""
        self._asset, self._platform = asset or asset_for()

    # ── check ───────────────────────────────────────────────────────────────

    def current_version(self) -> str:
        """Version of the *running* binary — the only source that cannot lie.

        The install record is bookkeeping: it may be synthetic (``local-build``
        from a ``--file`` install) or optimistic (written the moment a staged
        upgrade is handed off), while ``orzmc/version.py`` is baked in at build
        time. That also makes a failed hand-off self-healing: the next run
        still reports the old version and simply tries again.
        """
        return f"v{__version__}"

    def check(self, version: str | None = None) -> UpdateCheck:
        """Resolve the upgrade target: explicit ``version`` or the latest release.

        An explicit version never calls the GitHub API, which also makes it the
        escape hatch when the API is rate-limited (60 requests/hour/IP).
        """
        tag = normalize_tag(version) if version else self._latest_tag()
        current = self.current_version()
        return UpdateCheck(
            current=current,
            latest=tag,
            asset=self._asset,
            platform=self._platform,
            url=f"{DOWNLOAD_BASE}/{tag}/{self._asset}",
            available=is_newer(tag, current),
        )

    def _latest_tag(self) -> str:
        try:
            data = self._http.get_json(API_LATEST, headers=api_headers())
        except Exception as exc:
            raise RuntimeError(
                "无法获取最新版本信息(网络问题或 GitHub API 限流)。请指定版本重试:orzmc update --version vX.Y.Z"
            ) from exc
        tag = (data or {}).get("tag_name") if isinstance(data, dict) else None
        if not tag:
            raise RuntimeError("GitHub 返回的发布信息中没有版本标签,请用 --version 指定版本")
        return str(tag)

    # ── update ──────────────────────────────────────────────────────────────

    def update(
        self,
        version: str | None = None,
        *,
        file: str | None = None,
        yes: bool = False,
        force: bool = False,
        confirm: Callable[[str], bool] | None = None,
    ) -> UpdateCheck | None:
        """Replace the installed binary with the target release.

        Returns the check (``applied=True`` after a swap), ``None`` when the
        user declined, or the check with ``applied=False`` when the automatic
        path already runs the newest release. ``file`` installs a local binary
        instead of downloading one (offline / test seam) and requires
        ``version`` to record what it is.
        """
        if not self._binary:
            raise RuntimeError("无法确定当前二进制路径,请用 orzmc update 重新执行")
        self._guard(force)
        check = self._target(version, file)
        if version is None and file is None and not check.available:
            if normalize_tag(check.current) != check.latest:
                self._reporter.info(f"当前版本 {check.current} 比最新发布 {check.latest} 新,已跳过升级")
            else:
                self._reporter.info(f"已是最新版本 {check.current}")
            return check
        if not yes and confirm is not None and not confirm(f"升级 orzmc {check.current} → {check.latest}?"):
            return None

        staging = os.path.join(os.path.dirname(self._binary), STAGING_NAME)
        try:
            staged = self._stage(check, file)
            self._handoff(staged)
        except Exception:
            # A failed upgrade must never leave a half-staged file behind — but a
            # successful hand-off keeps it: the helper needs it to rename.
            self._fs.remove(staging)
            raise
        self._record(check, file)
        self._reporter.success(f"已升级到 {check.latest}(本命令退出后生效)")
        return replace(check, applied=True)

    # ── internals ───────────────────────────────────────────────────────────

    def _guard(self, force: bool) -> None:
        if is_pip_managed(self._binary):
            raise RuntimeError(
                "检测到 pip / pipx 安装的 orzmc-app,请用 pip install -U orzmc-app(或 pipx upgrade orzmc-app)升级"
            )
        if not force and looks_like_dev(self._binary):
            raise RuntimeError(
                "检测到疑似开发环境安装(venv / site-packages / PyInstaller 临时目录),已拒绝自升级;"
                "确认无误可加 --force 继续"
            )

    def _target(self, version: str | None, file: str | None) -> UpdateCheck:
        if file is None:
            return self.check(version)
        local = os.path.abspath(os.path.expanduser(file))
        if not self._fs.is_file(local):
            raise RuntimeError(f"本地文件不存在: {local}")
        if not version:
            raise RuntimeError("使用本地文件升级时必须同时指定版本:orzmc update --file <路径> --version vX.Y.Z")
        tag = normalize_tag(version)
        current = self.current_version()
        return UpdateCheck(
            current=current,
            latest=tag,
            asset=self._asset,
            platform=self._platform,
            url=None,
            available=tag != normalize_tag(current),
        )

    def _stage(self, check: UpdateCheck, file: str | None) -> str:
        """Put the new binary next to the old one (same volume → atomic rename)."""
        install_dir = os.path.dirname(self._binary)
        self._fs.ensure_dir(install_dir)
        staged = os.path.join(install_dir, STAGING_NAME)
        self._fs.remove(staged)
        if file is not None:
            self._reporter.info(f"使用本地文件升级: {os.path.abspath(os.path.expanduser(file))}")
            shutil.copyfile(os.path.abspath(os.path.expanduser(file)), staged)
        else:
            assert check.url is not None
            try:
                download_with_progress(
                    self._http, check.url, staged, self._sink, f"下载 orzmc {check.latest} ({check.asset})"
                )
            except Exception as exc:
                raise RuntimeError(f"下载 {check.asset} 失败: {exc}") from exc
        self._verify_binary(staged)
        os.chmod(staged, 0o755)  # the helper swaps it straight into place
        return staged

    @staticmethod
    def _verify_binary(path: str) -> None:
        """Reject empty/HTML/JSON payloads before anything is put in place."""
        try:
            with open(path, "rb") as handle:
                head = handle.read(4)
        except OSError as exc:
            raise RuntimeError(f"无法读取下载的二进制: {exc}") from exc
        if not head:
            raise RuntimeError("下载的二进制为空,已中止升级")
        allowed = _EXEC_MAGIC + ((b"MZ",) if os.name == "nt" else ())
        if not any(head.startswith(magic) for magic in allowed):
            raise RuntimeError("下载内容不是可执行文件(可能是错误页/校验失败),已中止升级")

    def _handoff(self, staged: str) -> None:
        """Hand the final rename to a detached helper (see the module docstring).

        The helper survives us because ``run_detached`` starts a new session,
        and it waits for *our* pid to disappear before touching the file we are
        still executing from.
        """
        command = applier_command(staged, self._binary, os.getpid())
        try:
            self._process.run_detached(command, cwd=os.path.dirname(self._binary))
        except OSError as exc:
            raise RuntimeError(f"无法启动升级助手,已保留旧版本: {exc}") from exc
        self._reporter.info("升级助手已就绪,将在本命令退出后完成替换")

    def _record(self, check: UpdateCheck, file: str | None) -> None:
        """Update the install record so the next run reports the new version."""
        found = InstallManifest.find(self._fs, binary=self._binary)
        if not found:
            self._reporter.warn("未找到安装记录,已替换二进制;建议重跑一键安装器补登记")
            return
        path, manifest = found
        replace(
            manifest,
            version=check.latest,
            platform=check.platform,
            source=None if file is not None else check.url,
        ).write(self._fs, path)
