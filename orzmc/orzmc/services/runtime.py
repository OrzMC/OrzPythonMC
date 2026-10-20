"""Runtime channel (M3): 下载 Swift 真身并**转发**命令(规范 `install-update.md` §10.1)。

PyPI 上的 ``orzmc`` 作为发行通道时只做三件事:**解析资产 → 下载校验 → 交给真身**。
命令语义一律不落在 Python 侧(否则又变成两份实现)。

设计约束(与规范一一对应):

- 资产来自 ``OrzMC/OrzMCKit`` 的 Release,名形 ``orzmc-<os>-<arch>``(Windows 是 ``.zip``);
- **必须**用同一 Release 的 ``SHA256SUMS`` 校验,失败/缺失都拒绝落盘(``E_VERIFY``);
- 落盘是**原子替换**(同目录临时文件 + rename),失败不破坏已有可用真身;
- 无网络且本地没有真身 → ``E_NETWORK``,由 CLI 提示回退 ``ORZMC_RUNTIME=python``;
- 真身不在 PATH 上(PATH 上只有 launcher),因此不违反 F1「PATH 上只有一个 ``orzmc``」。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from orzmc.infra.fs import FileStore, default_state_dir
from orzmc.infra.hashing import sha256_file
from orzmc.infra.http import HttpClient
from orzmc.infra.log import NullReporter, Reporter
from orzmc.infra.progress import NullProgress, ProgressSink
from orzmc.infra.transfer import download_with_progress

REPO = "OrzMC/OrzMCKit"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases"
DOWNLOAD_BASE = f"https://github.com/{REPO}/releases/download"

#: 开关变量:未设置 / ``python`` 走 Python 实现;``swift`` 走 shim 转发真身。
RUNTIME_ENV = "ORZMC_RUNTIME"
PAYLOAD_NAME = "orzmc"
SUMS_NAME = "SHA256SUMS"
#: ``install.conf`` 的 ``channel`` 字段值(区分「PATH 上是 launcher,真身在状态目录」)。
WRAPPER_CHANNEL = "wrapper"

# Mach-O + ELF;Windows 额外接受 MZ(PE)。
_EXEC_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
)


class RuntimeChannelError(RuntimeError):
    """带规范错误码的通道错误(CLI 直接透传到 ``error.code``)。"""

    def __init__(self, message: str, code: str = "E_RUNTIME") -> None:
        super().__init__(message)
        self.code = code


def _read_record(
    environ: Mapping[str, str] | None = None,
    is_windows: bool | None = None,
) -> dict[str, str]:
    """读状态目录的 ``install.conf``(不存在则返回空 dict)。"""
    from orzmc.services.selfinstall import MANIFEST_FILENAME

    path = os.path.join(default_state_dir(environ=environ, is_windows=is_windows), MANIFEST_FILENAME)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value
    return fields


def runtime_mode(environ: dict[str, str] | None = None) -> str:
    """**只看显式开关**:``swift`` / 其余按 ``python``。

    默认判定要读 ``install.conf``,走 :func:`resolve_runtime`。
    """
    env = os.environ if environ is None else environ
    value = (env.get(RUNTIME_ENV) or "").strip().lower()
    return "swift" if value == "swift" else "python"


def resolve_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    is_windows: bool | None = None,
) -> tuple[str, str]:
    """返回 ``(运行时, 判定依据)`` -- 规范 `install-update.md` §10.1。

    **Python 是实现主体**: 未显式开启时**一律** ``python``(不会因为"没装过"就去下载 Swift)。

    1. ``ORZMC_RUNTIME=swift`` -> ``swift``(实验开关, 仅显式设置才生效);
    2. ``ORZMC_RUNTIME=python`` -> ``python``(显式钉住, 与 1 等价于"没开");
    3. 未设置 + ``channel=wrapper`` -> ``swift``(用户自己跑过 ``runtime install``, 尊重已有选择);
    4. 未设置 + ``source`` 指向 OrzMCKit -> ``swift``(用户自己装了 Swift 原生版, 复用它);
    5. 其余(含**全新安装**) -> ``python``.
    """
    env = os.environ if environ is None else environ
    explicit = (env.get(RUNTIME_ENV) or "").strip().lower()
    if explicit == "swift":
        return "swift", "env:swift"
    if explicit == "python":
        return "python", "env:python"
    record = _read_record(environ=env, is_windows=is_windows)
    if record.get("channel") == WRAPPER_CHANNEL:
        return "swift", "record:wrapper"
    if any(marker in record.get("source", "") for marker in _SWIFT_SOURCES):
        return "swift", "record:native"
    return "python", "default"


def payload_asset(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    """``(资产名, platform 标签)``;与 Swift 侧 `release.yml` 的命名一致。"""
    os_name = (system or platform.system()).lower()
    arch = (machine or platform.machine()).lower()
    if os_name == "darwin":
        os_name = "macos"
    elif os_name not in ("linux", "windows"):
        raise RuntimeChannelError(f"运行时分发暂不支持该系统: {system or platform.system()}", "E_UNSUPPORTED")
    if arch in ("arm64", "aarch64"):
        arch = "arm64"
    elif arch in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        raise RuntimeChannelError(f"运行时分发暂不支持该架构: {machine or platform.machine()}", "E_UNSUPPORTED")
    suffix = ".zip" if os_name == "windows" else ""
    return f"orzmc-{os_name}-{arch}{suffix}", f"{os_name}-{arch}"


def payload_dir(environ: dict[str, str] | None = None, is_windows: bool | None = None) -> str:
    """真身目录:``<stateDir>/bin``(不在 PATH 上)。"""
    return os.path.join(default_state_dir(environ=environ, is_windows=is_windows), "bin")


def payload_path(
    environ: dict[str, str] | None = None,
    is_windows: bool | None = None,
) -> str:
    windows = (os.name == "nt") if is_windows is None else is_windows
    return os.path.join(payload_dir(environ=environ, is_windows=is_windows), PAYLOAD_NAME + (".exe" if windows else ""))


#: 哪些 ``source`` 值代表"Swift 真身"(可安全复用)。
_SWIFT_SOURCES = ("OrzMCKit",)


def _is_swift_payload(source: str, channel: str) -> bool:
    """``source`` 指向 OrzMCKit(或本通道自己的 wrapper 记录)才算真身。

    排除 ``OrzMC/OrzPythonMC``:那是 Python 实现,复用它会变成"自己转自己"。
    """
    if channel == WRAPPER_CHANNEL:
        return True
    return any(marker in source for marker in _SWIFT_SOURCES)


@dataclass
class RuntimeStatus:
    """``orzmc runtime status`` 的数据形状。"""

    enabled: bool
    installed: bool
    version: str
    path: str
    source: str
    asset: str
    #: 是否**复用**了已有的 Swift 原生安装(而不是本通道自己的副本)。
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "installed": self.installed,
            "version": self.version,
            "path": self.path,
            "source": self.source,
            "asset": self.asset,
            # launcher 自身版本(规范 §10.1:版本号语义与真身分开)。
            # 是否复用了已有的原生 Swift 安装(§10.1 共存规则)。
            "reused": self.reused,
            "wrapper": _wrapper_version(),
        }


def _wrapper_version() -> str:
    from orzmc.version import __version__

    return __version__


def _normalize_tag(version: str) -> str:
    cleaned = version.strip()
    if not cleaned:
        raise RuntimeChannelError("版本号不能为空", "E_USAGE")
    return cleaned if cleaned.startswith("v") else f"v{cleaned}"


def parse_sha256sums(text: str) -> dict[str, str]:
    """``<hex>  <filename>`` 行解析(GNU/BSD 两种分隔都吃)。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def looks_like_executable(path: str, is_windows: bool | None = None) -> bool:
    """魔数校验:GitHub 的 HTML 错误页绝不能被当成真身。"""
    windows = (os.name == "nt") if is_windows is None else is_windows
    allowed = _EXEC_MAGIC + ((b"MZ",) if windows else ())
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in allowed)


class RuntimeChannel:
    """解析 / 安装 / 转发 Swift 真身。

    依赖全部可注入(http / fs / reporter / sink / 平台),所以单测不需要网络。
    """

    def __init__(
        self,
        http: HttpClient | None = None,
        fs: FileStore | None = None,
        reporter: Reporter | None = None,
        sink: ProgressSink | None = None,
        *,
        environ: dict[str, str] | None = None,
        system: str | None = None,
        machine: str | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self._http = http or HttpClient()
        self._fs = fs or FileStore()
        self._reporter = reporter or NullReporter()
        self._sink = sink or NullProgress()
        self._env = dict(os.environ if environ is None else environ)
        self._asset, self._platform = payload_asset(system, machine)
        self._windows = (os.name == "nt") if is_windows is None else is_windows

    # ── 定位 ────────────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return payload_path(environ=self._env, is_windows=self._windows)

    @property
    def asset(self) -> str:
        return self._asset

    def _record(self) -> dict[str, str]:
        return _read_record(environ=self._env, is_windows=self._windows)

    def resolve(self) -> tuple[str, str]:
        """返回**要执行的真身** ``(path, source)``。

        优先级(规范 `install-update.md` §10.1「与原生安装共存」):

        1. `install.conf` 记录的真身 —— 若是 Swift 原生安装(``source`` 指向 OrzMCKit)
           且文件真的可执行,就**复用它**,不再下载第二份;
        2. 否则用本通道自己的副本 ``<stateDir>/bin/orzmc``(不存在则装上)。

        **绝不复用 Python 的二进制**:那会转发给 Python 实现,变成自己转自己。
        """
        record = self._record()
        recorded = record.get("binary", "")
        source = record.get("source", "")
        own = self.path
        if recorded and _is_swift_payload(source, record.get("channel", "")) and self._runnable(recorded):
            if recorded != own:
                # 别人装的 Swift 原生版:直接用(它的备份与更新由那条通道负责,我们不插手)。
                return recorded, source
            # 是**我们自己的副本**:跑之前做魔数预检,坏了就用 .bak 回滚(§10.1)。
            usable = self._verified_payload(recorded)
            if usable is not None:
                return usable, source
            return own, source  # 坏且无备份:交给 forward 报 E_RUNTIME 并提示重装
        if self._fs.is_file(own):
            usable = self._verified_payload(own)
            if usable is not None:
                return usable, (source if recorded == own else f"github:{REPO}")
        return own, f"github:{REPO}"

    @property
    def backup_path(self) -> str:
        """旧真身的备份路径 (同一目录 rename, 跨盘不会发生)."""
        return self.path + ".bak"

    def _replace_payload(self, staged: str) -> None:
        """把新真身原子换上去; **先把旧的改名成 .bak**, 失败则还原 (规范 §10.1 回滚)."""
        backup = self.backup_path
        had_old = self._fs.is_file(self.path)
        if had_old:
            try:
                if self._fs.is_file(backup):
                    os.remove(backup)
                os.replace(self.path, backup)
            except OSError as exc:  # 备份失败就别动现有的 (安全优先)
                raise RuntimeChannelError(f"无法备份旧真身: {exc}", "E_RUNTIME") from exc
        try:
            os.replace(staged, self.path)
        except OSError as exc:
            if had_old:
                try:
                    os.replace(backup, self.path)
                except OSError:
                    pass
            raise RuntimeChannelError(f"写入真身失败: {exc}", "E_RUNTIME") from exc

    def _recover_from_backup(self, reason: str) -> str | None:
        """用 `.bak` 还原真身; 成功返回路径, 否则 ``None`` (规范 §10.1 回滚)."""
        backup = self.backup_path
        if not (self._fs.is_file(backup) and looks_like_executable(backup, self._windows)):
            return None
        try:
            os.replace(backup, self.path)
        except OSError:
            return None
        self._reporter.warn(f"真身不可用({reason}),已回滚到上一份: {self.path}")
        return self.path

    def _verified_payload(self, path: str) -> str | None:
        """跑之前的魔数预检: 坏文件 + 好 `.bak` -> 回滚, 返回可用路径."""
        if not self._fs.is_file(path):
            return None
        if looks_like_executable(path, self._windows):
            return path
        return self._recover_from_backup("魔数校验失败")

    def _runnable(self, path: str) -> bool:
        if not self._fs.is_file(path):
            return False
        if self._windows:
            return True
        return os.access(path, os.X_OK)

    def status(self) -> RuntimeStatus:
        record = self._record()
        own = self.path
        recorded = record.get("binary", "")
        source = record.get("source", "")
        shared = bool(recorded) and _is_swift_payload(source, record.get("channel", "")) and self._runnable(recorded)
        path = recorded if shared else own
        installed = self._fs.is_file(path)
        version = record.get("version", "") if installed and recorded == path else ""
        return RuntimeStatus(
            enabled=resolve_runtime(self._env, is_windows=self._windows)[0] == "swift",
            installed=installed,
            version=version,
            path=path,
            source=(source if installed else ""),
            asset=self._asset,
            reused=shared,
        )

    # ── 安装 ────────────────────────────────────────────────────────────────

    def _latest_tag(self) -> str:
        """实验通道:Swift 的 Release **一律是 pre-release**,`/releases/latest` 不会指向它们,
        所以优先**列出** Release(新→旧,跳过 draft 与不带资产的),再退回 `/releases/latest` 与 302。
        """
        try:
            listed = self._http.get_json(f"{API_RELEASES}?per_page=10")
            entries = listed if isinstance(listed, list) else []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("draft"):
                    continue
                tag = str(entry.get("tag_name") or "")
                if tag:
                    return _normalize_tag(tag)
        except Exception:
            self._reporter.debug("列 Release 失败,退回 /releases/latest")
        try:
            payload = self._http.get_json(API_LATEST)
            tag = str(payload.get("tag_name") or "")
            if tag:
                return _normalize_tag(tag)
        except Exception:
            self._reporter.debug("GitHub API 不可用,改走 releases/latest 的 302")
        location = self._http.head_location(f"https://github.com/{REPO}/releases/latest")
        marker = "/releases/tag/"
        if location and marker in location:
            return _normalize_tag(location.split(marker, 1)[1].split("/")[0].split("?")[0])
        raise RuntimeChannelError("无法确定最新版本(网络不可用或受限)", "E_NETWORK")

    def install(self, version: str | None = None) -> RuntimeStatus:
        """下载 + 校验 + 原子落盘;返回安装后的状态。

        已有**可复用的原生 Swift 安装**时直接返回它(不下载、不覆盖记录):
        同一台机器上只该有一份真身,重复下载既浪费也会让 `install.conf` 漂移。
        要强制换成自管副本,先删记录(或走原生安装器的 `update`)。
        """
        recorded = self._record()
        native = recorded.get("binary", "")
        # 只在「**别人装的** Swift 原生安装」时 no-op(别在同一台机器上再存一份);
        # 本通道自己的副本(channel=wrapper)要允许重装/升级,否则永远换不了新版本。
        reusable = recorded.get("channel") != WRAPPER_CHANNEL and _is_swift_payload(
            recorded.get("source", ""), recorded.get("channel", "")
        )
        if native and reusable and self._runnable(native):
            self._reporter.info(f"复用已有的 Swift 安装: {native}")
            return self.status()

        tag = _normalize_tag(version) if version else self._latest_tag()
        base = f"{DOWNLOAD_BASE}/{tag}"
        try:
            sums = parse_sha256sums(self._http.get_text(f"{base}/{SUMS_NAME}"))
        except Exception as exc:
            raise RuntimeChannelError(f"取不到 {tag} 的 {SUMS_NAME}: {exc}", "E_NETWORK") from exc
        expected = sums.get(self._asset)
        if not expected:
            raise RuntimeChannelError(
                f"{tag} 没有 {self._asset}(该平台资产未发布);可用: {', '.join(sorted(sums)) or '无'}",
                "E_NOT_FOUND",
            )

        target_dir = payload_dir(environ=self._env, is_windows=self._windows)
        self._fs.ensure_dir(target_dir)
        staging = tempfile.mkdtemp(prefix="orzmc-runtime-", dir=target_dir)
        try:
            downloaded = os.path.join(staging, self._asset)
            download_with_progress(self._http, f"{base}/{self._asset}", downloaded, self._sink, self._asset)
            actual = sha256_file(downloaded)
            if actual != expected:
                raise RuntimeChannelError(
                    f"{self._asset} 校验失败(期望 {expected[:12]}…,实际 {actual[:12]}…)", "E_VERIFY"
                )
            staged = self._unpack(downloaded, staging, target_dir)
            if not looks_like_executable(staged, self._windows):
                raise RuntimeChannelError(f"{self._asset} 解出来的不是可执行文件", "E_VERIFY")
            if not self._windows:
                os.chmod(staged, 0o755)
            self._replace_payload(staged)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        self._write_record(tag)
        self._reporter.success(f"已就绪: {self.path}({tag})")
        return self.status()

    def _unpack(self, archive: str, staging: str, target_dir: str) -> str:
        """Windows 的资产是 zip(exe + 运行时 DLL);其余平台是裸二进制。

        DLL 必须与 exe 同行(缺了会 STATUS_DLL_NOT_FOUND),所以先用 os.replace 把它们
        落到 target_dir,再让调用方原子替换 exe(替换失败也还有旧 exe 顶着)。
        """
        if not self._asset.endswith(".zip"):
            return archive
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if PAYLOAD_NAME + ".exe" not in names:
                raise RuntimeChannelError(f"{self._asset} 里没有 {PAYLOAD_NAME}.exe", "E_VERIFY")
            bundle.extractall(staging)
        for name in names:
            if not name.lower().endswith(".dll"):
                continue
            os.replace(os.path.join(staging, name), os.path.join(target_dir, name))
        return os.path.join(staging, PAYLOAD_NAME + ".exe")

    def _write_record(self, tag: str) -> None:
        from orzmc.services.selfinstall import MANIFEST_FILENAME, InstallManifest

        record = InstallManifest(
            version=tag.lstrip("v"),
            platform=self._platform,
            install_dir=os.path.dirname(self.path),
            binary=self.path,
            source=f"github:{REPO}",
            surface="cli",
            channel=WRAPPER_CHANNEL,
        )
        record.write(
            self._fs,
            os.path.join(default_state_dir(environ=self._env, is_windows=self._windows), MANIFEST_FILENAME),
        )

    # ── 转发 ────────────────────────────────────────────────────────────────

    def ensure(self, version: str | None = None) -> str:
        """有可用真身就直接用;没有则装一次(装不了就按规范报错)。"""
        path, _ = self.resolve()
        if self._fs.is_file(path):
            return path
        try:
            return self.install(version).path
        except RuntimeChannelError:
            raise
        except Exception as exc:
            raise RuntimeChannelError(
                f"拿不到 Swift 运行时可执行文件: {exc};可设 ORZMC_RUNTIME=python 回退",
                "E_NETWORK",
            ) from exc

    def forward(self, argv: list[str], binary: str | None = None) -> int:
        """把 argv 原样交给真身,并等价转发退出码。

        POSIX 用 ``execv``(进程被替换,信号/退出码天然一致);Windows 没有 ``exec``,
        用子进程转发并把 ``Ctrl-C`` 传下去。
        """
        target = binary or self.ensure()
        if os.name != "nt":
            try:
                os.execv(target, [target, *argv])  # pragma: no cover - 进程被替换,after 不可达
            except OSError as exc:
                # 文件本身不可执行 (被截断 / 权限坏了 / 不是本机架构) -> 用 .bak 回滚后重试一次.
                recovered = self._recover_from_backup(f"执行失败: {exc}")
                if recovered is None:
                    raise RuntimeChannelError(
                        f"无法执行真身 {target}: {exc};可运行 `orzmc runtime install` 重装",
                        "E_RUNTIME",
                    ) from exc
                os.execv(recovered, [recovered, *argv])  # pragma: no cover
        try:
            return subprocess.call([target, *argv])
        except KeyboardInterrupt:  # pragma: no cover - 交互中断
            return 130
        except OSError as exc:
            recovered = self._recover_from_backup(f"执行失败: {exc}")
            if recovered is None:
                raise RuntimeChannelError(
                    f"无法执行真身 {target}: {exc};可运行 `orzmc runtime install` 重装",
                    "E_RUNTIME",
                ) from exc
            return subprocess.call([recovered, *argv])


def _stderr_reporter() -> tuple[Reporter, ProgressSink]:
    """首次运行要下载 10+MB:进度与日志一律走 **stderr**(stdout 留给真身的输出)。"""
    try:
        from rich.console import Console

        from orzmc.infra.log import RichReporter
        from orzmc.infra.progress import RichProgress

        console = Console(stderr=True)
        return RichReporter(console=console), RichProgress(console=console)
    except Exception:  # pragma: no cover - 缺 rich 时退化为静默
        return NullReporter(), NullProgress()


def forward_or_report(argv: list[str]) -> int:
    """CLI 入口用的薄包装:把通道错误翻成规范错误码。"""
    reporter, sink = _stderr_reporter()
    channel = RuntimeChannel(reporter=reporter, sink=sink)
    return channel.forward(argv)


__all__ = [
    "PAYLOAD_NAME",
    "REPO",
    "RUNTIME_ENV",
    "WRAPPER_CHANNEL",
    "RuntimeChannel",
    "RuntimeChannelError",
    "RuntimeStatus",
    "forward_or_report",
    "looks_like_executable",
    "parse_sha256sums",
    "payload_asset",
    "payload_dir",
    "payload_path",
    "resolve_runtime",
    "runtime_mode",
]
