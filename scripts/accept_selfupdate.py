#!/usr/bin/env python3
"""Cross-platform acceptance harness for the installer + ``orzmc update``.

Asserts exactly what the GitHub Actions ``installer`` job cares about, on any
platform (the job runs this script on all six runners, and on Windows once per
PowerShell flavour — ``powershell`` 5.1 and ``pwsh`` 7.x):

1. build the PyInstaller onefile (unless ``--skip-build``),
2. report the PowerShell runtime in use (Windows; ``--expect-ps-major`` pins it),
3. install it with the real one-line installer via the ``--file`` seam, into a
   temp dir with the state dir redirected — PATH / shell rc / ``~/minecraft``
   are never touched,
4. ``update --check`` reports current + latest without installing anything,
5. ``update --file <system executable> -v v9.9.9 --yes`` really swaps the
   binary: the deferred helper renames the staged file after we exit, so the
   target's sha256 must become the payload's and the staging file must vanish,
6. ``update -v <latest tag>`` downloads a real release asset and lands it
   (``--skip-download`` for offline machines),
7. a nonexistent version fails without damaging the installed binary,
8. a dev-style path (``.venv``) is refused by the guard,
9. the documented *piped* entry points (``irm … | iex`` / ``curl … | sh``) work
   when served like GitHub Pages does — ``application/octet-stream`` for
   ``.ps1``, which is what makes PS 5.1 mis-decode the source unless the
   encoding self-heal kicks in; the check requires correctly decoded Chinese
   and a forwarded ``-file/-dir`` argument set (``--skip-oneline`` opts out),
10. ``self-uninstall --yes`` removes both the binary and the install record.

Why the swap is deferred (validated here, not just documented): a running
PyInstaller onefile binary reads its PYZ lazily from ``<executable>?<offset>``,
so replacing its own file breaks the next not-yet-imported module. The helper
also sidesteps Windows' "running exe cannot be overwritten" rule.

Everything lives under one temp dir and is removed at the end (``--keep``
preserves it for inspection). Exit code is non-zero iff any step failed.

Run from the workspace root::

    uv run --package orzmc-app python scripts/accept_selfupdate.py
    uv run --package orzmc-app python scripts/accept_selfupdate.py --skip-build --skip-download
    # Windows: exercise each PowerShell flavour explicitly
    uv run python scripts/accept_selfupdate.py --skip-build --powershell powershell --expect-ps-major 5
    uv run python scripts/accept_selfupdate.py --skip-build --powershell pwsh --expect-ps-major 7
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import http.server
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
REPO = "OrzMC/OrzPythonMC"
RELEASES_LATEST = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = "orzmc-acceptance"
# Every PowerShell child must emit UTF-8 (its default is the console code page,
# which mangles Chinese into "?" before we can assert on it) and must fail the
# process on an uncaught throw — like `-File` does.
PS_UTF8 = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8;"
PS_WRAP = "{preamble} try {{ {body} }} catch {{ Write-Error $_; exit 1 }}"
IS_WINDOWS = os.name == "nt"
BINARY_NAME = "orzmc.exe" if IS_WINDOWS else "orzmc"
BUILT_BINARY = ROOT / "dist" / BINARY_NAME
STAGING_NAME = ".orzmc-update.tmp"
SWAP_TIMEOUT = 30.0  # seconds to wait for the detached helper to rename
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_checks: list[tuple[str, bool, str]] = []
_verbose = False


def force_utf8_stdio() -> None:
    """Windows consoles default to cp1252; every message here is Chinese."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


def say(message: str) -> None:
    print(message, flush=True)


def die(message: str) -> NoReturn:
    say(f"错误:{message}")
    raise SystemExit(2)


def check(name: str, passed: bool, detail: str = "") -> bool:
    """Record one acceptance verdict (kept going so every failure is visible).

    A failing check prints its diagnostics *in full* (truncated): on CI the
    interesting part (an installer error, a PowerShell exception, the helper's
    log) is never the last line.
    """
    _checks.append((name, passed, detail))
    mark = "OK  " if passed else "FAIL"
    say(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not passed and detail and "\n" in detail:
        for line in detail.splitlines()[:60]:
            say(f"      | {line}")
    return passed


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    """Everything a child said, for diagnostics."""
    return plain(f"$ {result.args}\n--- exit {result.returncode} ---\n{result.stdout}{result.stderr}")


def helper_log(install_dir: Path) -> str:
    """The self-update helper's own log (it prints one terminal DONE/FAILED line)."""
    log = install_dir / ".orzmc-update.log"
    try:
        return log.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "(助手日志缺失)"


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command in the repo root; always captures output (no TTY prompts)."""
    if _verbose:
        say(f"$ {' '.join(args)}")
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        args,
        cwd=str(ROOT),
        env=merged,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def ps_command(powershell: str, body: str) -> list[str]:
    """``<ps> -Command`` with UTF-8 output and a non-zero exit on any throw."""
    return [powershell, "-NoProfile", "-NonInteractive", "-Command", PS_WRAP.format(preamble=PS_UTF8, body=body)]


def latest_release_tag() -> str:
    """Newest release tag via the public redirect — no API, so no rate limit."""
    import urllib.request

    request = urllib.request.Request(RELEASES_LATEST, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = response.geturl()
    except Exception:
        return ""
    return final.rstrip("/").rsplit("/", 1)[-1] if "/tag/" in final else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_helper(install_dir: Path, timeout: float = SWAP_TIMEOUT) -> str:
    """Wait for the detached helper's terminal line; returns its log."""
    log = install_dir / ".orzmc-update.log"

    def finished() -> bool:
        return log.is_file() and any(marker in helper_log(install_dir) for marker in ("DONE", "FAILED"))

    wait_for(finished, timeout)
    return helper_log(install_dir)


def wait_for(predicate, timeout: float = SWAP_TIMEOUT) -> bool:
    """Poll ``predicate`` until true (the swap happens after our process exits)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return predicate()


def plain(text: str) -> str:
    return ANSI.sub("", text)


def payload_path() -> Path:
    """A real, distinguishable executable shipped by the OS (proves the swap)."""
    if IS_WINDOWS:
        system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
        for name in ("where.exe", "notepad.exe"):
            candidate = Path(system_root) / "System32" / name
            if candidate.is_file():
                return candidate
    else:
        for name in ("/bin/echo", "/usr/bin/true"):
            candidate = Path(name)
            if candidate.is_file():
                return candidate
    die("找不到可用于替换验证的系统可执行文件")


class Sandbox:
    """Temp install dir + redirected state dir; nothing outside is touched."""

    def __init__(self, keep: bool) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="orzmc-accept-selfupdate-"))
        self.install_dir = self.root / "bin"
        self.oneline_dir = self.root / "bin-oneline"
        self.iex_dir = self.root / "bin-iex"
        self.state_base = self.root / "state"
        self.oneline_state_base = self.root / "state-oneline"
        self.iex_state_base = self.root / "state-iex"
        self.game_root = self.root / "minecraft"
        self.keep = keep
        self.binary = self.install_dir / BINARY_NAME
        self.staging = self.install_dir / STAGING_NAME
        self.manifest = self.state_base / "orzmc" / "install.conf"

    def env(self) -> dict[str, str]:
        # XDG_STATE_HOME (Unix) / LOCALAPPDATA (Windows) is what the installer
        # and InstallManifest.find agree on; ORZMC_NO_RC keeps PATH untouched and
        # ORZMC_ROOT_DIR keeps even the recorded game root inside the sandbox.
        return {
            "XDG_STATE_HOME": str(self.state_base),
            "LOCALAPPDATA": str(self.state_base),
            "ORZMC_NO_RC": "1",
            "ORZMC_ROOT_DIR": str(self.game_root),
        }

    def env_oneline(self) -> dict[str, str]:
        """Same isolation, but its own state dir (the piped install owns that record)."""
        return {
            **self.env(),
            "XDG_STATE_HOME": str(self.oneline_state_base),
            "LOCALAPPDATA": str(self.oneline_state_base),
        }

    def env_iex(self) -> dict[str, str]:
        """Env seams for the literal ``irm … | iex`` run (no args can be passed there)."""
        return {
            **self.env(),
            "XDG_STATE_HOME": str(self.iex_state_base),
            "LOCALAPPDATA": str(self.iex_state_base),
            "ORZMC_BIN": str(self.iex_dir),
        }

    def install_local(self, powershell: str) -> subprocess.CompletedProcess[str]:
        """Install the freshly built binary through the real one-line installer."""
        if IS_WINDOWS:
            script = ROOT / "docs" / "install.ps1"
            body = f"& '{script}' -file '{BUILT_BINARY}' -dir '{self.install_dir}'"
            return run(ps_command(powershell, body), env=self.env())
        return run(
            [
                "sh",
                str(ROOT / "docs" / "install.sh"),
                "--file",
                str(BUILT_BINARY),
                "--dir",
                str(self.install_dir),
                "--no-modify-rc",
            ],
            env=self.env(),
        )

    def orzmc(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run([str(self.binary), *args], env=self.env())

    def remove(self) -> None:
        if self.keep:
            say(f"保留临时目录:{self.root}")
            return
        shutil.rmtree(self.root, ignore_errors=True)


def staged_site(sandbox: Sandbox) -> Path:
    """A copy of ``docs/`` shaped like the deployed GitHub Pages artifact.

    ``pages.yml`` strips the UTF-8 BOM from ``install.ps1`` before uploading;
    the repo file keeps it so PS 5.1 can read the script with ``-File``. Serving
    the repo file here would test a shape users never get, and the first line
    would become a bogus command ("The term 'Windows' is not recognized" — the
    very failure d4e049b fixed). Mirror the deploy step instead, and assert both
    halves of that pair.
    """
    site = sandbox.root / "site"
    shutil.rmtree(site, ignore_errors=True)
    shutil.copytree(ROOT / "docs", site)
    served = site / "install.ps1"
    body = served.read_bytes()
    check("仓库 install.ps1 保留 UTF-8 BOM(-File 需要)", body.startswith(b"\xef\xbb\xbf"))
    served.write_bytes(body[3:] if body.startswith(b"\xef\xbb\xbf") else body)
    check("部署形态已剥 BOM(镜像 pages.yml)", not served.read_bytes().startswith(b"\xef\xbb\xbf"))
    return site


@contextlib.contextmanager
def serve_docs(directory: Path) -> Iterator[str]:
    """Serve ``directory`` on loopback with GitHub-Pages-like content types.

    ``.ps1`` has no MIME mapping, so ``http.server`` answers
    ``application/octet-stream`` — exactly what GitHub Pages does, which is what
    makes PS 5.1's ``irm`` decode the source byte-by-byte (and hence what the
    installer's encoding self-heal must recover from).
    """

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep the harness output clean
            pass

    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def step_build(skip: bool) -> None:
    say("== 1) 构建 PyInstaller 单文件二进制 ==")
    if skip:
        if not BUILT_BINARY.is_file():
            die(f"--skip-build 但没有 {BUILT_BINARY.relative_to(ROOT)}")
        say(f"  复用 {BUILT_BINARY.relative_to(ROOT)}")
        return
    result = run([sys.executable, str(ROOT / "scripts" / "build.py")])
    if result.returncode != 0 or not BUILT_BINARY.is_file():
        say(result.stdout[-2000:] + result.stderr[-2000:])
        die("构建失败")
    say(f"  已生成 {BUILT_BINARY.relative_to(ROOT)} ({BUILT_BINARY.stat().st_size} 字节)")


def _shell_label(powershell: str) -> str:
    """``powershell`` → ``Windows PowerShell 5.1``, ``pwsh`` → ``PowerShell 7.x``."""
    return "PowerShell 7.x" if Path(powershell).name.lower().startswith("pwsh") else "Windows PowerShell 5.1"


def step_shell_info(sandbox: Sandbox, powershell: str, expect_major: int | None) -> None:
    """Report (and optionally pin) which PowerShell flavour is under test."""
    if not IS_WINDOWS:
        return
    say(f"== 2) PowerShell 运行时({powershell}) ==")
    result = run(
        ps_command(powershell, "$PSVersionTable.PSVersion.ToString() + '|' + $PSVersionTable.PSEdition"),
        env=sandbox.env(),
    )
    info = plain(result.stdout + result.stderr).strip()
    check(f"{powershell} 可执行并能报告版本", result.returncode == 0 and "|" in info, info)
    if expect_major is not None:
        major = info.split("|")[0].split(".")[0]
        check(f"{powershell} 主版本为 {expect_major}", major == str(expect_major), info)


def step_install(sandbox: Sandbox, powershell: str) -> None:
    flavour = f"{_shell_label(powershell)} 运行" if IS_WINDOWS else "sh 运行"
    say(f"== 3) 一键安装器安装到隔离目录(--file,不动 PATH / rc;{flavour}) ==")
    result = sandbox.install_local(powershell)
    if result.returncode != 0 or not sandbox.binary.is_file():
        say(result.stdout + result.stderr)
        die("安装器失败")
    if not check("安装后二进制存在且可执行", os.access(sandbox.binary, os.X_OK)):
        return
    version = sandbox.orzmc("version")
    check("安装后可直接运行 `orzmc version`", version.returncode == 0, plain(version.stdout).strip())
    record = sandbox.manifest.read_text(encoding="utf-8-sig") if sandbox.manifest.is_file() else ""
    check("安装记录已写入 state 目录", "version=local-build" in record, f"{sandbox.manifest.parent.name}/install.conf")


def step_check(sandbox: Sandbox, tag: str) -> str:
    """``--check`` in both flavours.

    The explicit-tag flavour is strict (no API involved); the API flavour is
    what users hit first, but the unauthenticated limit (60/h/IP) is shared with
    everything else on the runner's egress IP, so a rate limit is reported as a
    warning — the documented fallback is exactly ``--version vX.Y.Z``.
    """
    say("== 4) update --check(只查询,不下载) ==")
    before = sha256(sandbox.binary)
    if tag:
        strict = sandbox.orzmc("update", "--check", "-v", tag)
        text = plain(strict.stdout + strict.stderr)
        check(f"--check -v {tag} 成功并打印版本", strict.returncode == 0 and tag in text, text.strip())
    else:
        say("  [WARN] 无法解析最新 tag(网络?),跳过显式版本检查")
    api = sandbox.orzmc("update", "--check")
    api_text = plain(api.stdout + api.stderr).strip()
    if api.returncode == 0:
        check("--check 走 GitHub API 成功", True, api_text.replace("\n", " ")[:90])
    elif "无法获取最新版本信息" in api_text:
        say(f"  [WARN] GitHub API 暂不可用(限流?);库已提供 --version 回退:{api_text.splitlines()[-1][:80]}")
    else:
        check("--check API 路径", False, api_text)
    check("--check 不会改动二进制", sha256(sandbox.binary) == before)
    return tag


def step_swap_from_file(sandbox: Sandbox) -> None:
    say("== 5) update --file <系统可执行文件>(验证延迟替换真的落地) ==")
    payload = payload_path()
    before = sha256(sandbox.binary)
    result = sandbox.orzmc("update", "--file", str(payload), "-v", "v9.9.9", "--yes")
    say(f"  {' '.join(plain(result.stdout + result.stderr).split())}")
    check("update 命令成功交接", result.returncode == 0, output_of(result) if result.returncode else "exit=0")
    log = wait_for_helper(sandbox.install_dir)
    swapped = sandbox.binary.is_file() and sha256(sandbox.binary) == sha256(payload)
    check("助手已把目标文件换成新二进制(sha256 一致)", swapped, f"助手日志: {log}")
    check("暂存文件已清理", not sandbox.staging.exists())
    check("旧文件确实被换掉", sha256(sandbox.binary) != before)
    record = sandbox.manifest.read_text(encoding="utf-8-sig")
    check("安装记录版本已改写", "version=v9.9.9" in record)


def step_download(sandbox: Sandbox, latest: str, skip: bool, powershell: str) -> None:
    say("== 6) update -v <最新 tag>(真实下载 release 资产) ==")
    if skip or not latest:
        say("  已跳过(--skip-download 或未解析到 tag)")
        return
    sandbox.install_local(powershell)
    before = sha256(sandbox.binary)
    result = sandbox.orzmc("update", "-v", latest, "--yes")
    say(f"  {' '.join(plain(result.stdout + result.stderr).split())}")
    check("真实下载升级命令成功", result.returncode == 0, output_of(result) if result.returncode else "exit=0")
    log = wait_for_helper(sandbox.install_dir)
    swapped = sha256(sandbox.binary) != before and not sandbox.staging.exists()
    check("下载的新二进制已落地且暂存已清理", swapped, f"助手日志: {log}")
    check("新二进制可运行", sandbox.orzmc("version").returncode == 0)
    record = sandbox.manifest.read_text(encoding="utf-8-sig")
    check("安装记录含 source(下载地址)", "source=https://github.com/" in record, record.replace("\n", " ")[-120:])


def step_bad_version(sandbox: Sandbox, powershell: str) -> None:
    say("== 7) 失败路径:不存在的版本不得破坏现有二进制 ==")
    sandbox.install_local(powershell)
    before = sha256(sandbox.binary)
    result = sandbox.orzmc("update", "-v", "v0.0.0", "--yes")
    check("不存在的版本以非 0 退出", result.returncode != 0, f"exit={result.returncode}")
    check("旧二进制未被破坏", sha256(sandbox.binary) == before and sandbox.orzmc("version").returncode == 0)
    check("没有残留暂存文件", not sandbox.staging.exists())


def step_dev_guard(sandbox: Sandbox) -> None:
    say("== 8) 开发环境护栏:venv 路径必须被拒绝 ==")
    dev_bin = sandbox.root / "dev" / ".venv" / "bin" / BINARY_NAME
    dev_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUILT_BINARY, dev_bin)
    dev_bin.chmod(dev_bin.stat().st_mode | stat.S_IXUSR)
    before = sha256(dev_bin)
    result = run([str(dev_bin), "update", "--file", str(BUILT_BINARY), "-v", "v9.9.9", "--yes"], env=sandbox.env())
    check("venv 内的 update 被拒绝", result.returncode != 0, f"exit={result.returncode}")
    check("被拒绝时未改动该二进制", sha256(dev_bin) == before)
    check("提示开发环境护栏", "开发环境" in plain(result.stdout + result.stderr))


def step_oneline(sandbox: Sandbox, powershell: str, skip: bool, skip_download: bool) -> None:
    """The documented piped entry points, served the way GitHub Pages serves them.

    Windows gets two variants of the same story (both under the selected
    PowerShell flavour):

    * the scriptblock form from the installer header
      (``& ([scriptblock]::Create((irm …))) -file … -dir …``) — exercises the
      caller-scope semantics, ``$args`` forwarding through the self-heal, and
      the octet-stream mis-decode of PS 5.1, without downloading a release asset;
    * the literal ``irm … | iex`` one-liner, redirected entirely through the
      ``ORZMC_*`` env seams (it cannot take arguments) — the exact command the
      docs tell users to paste, including the real release download.

    The Chinese assertion is the prize: an ``application/octet-stream`` ``.ps1``
    is decoded byte-by-byte by PS 5.1 unless the encoding self-heal recovers the
    real UTF-8 source.
    """
    label = "irm | iex" if IS_WINDOWS else "curl | sh"
    say(f"== 9) 管道入口({label};模拟 GitHub Pages 的 Content-Type) ==")
    if skip:
        say("  已跳过(--skip-oneline)")
        return
    with serve_docs(staged_site(sandbox)) as base:
        if IS_WINDOWS:
            url = f"{base}/install.ps1"
            # Diagnostic: what does `irm` hand back for an octet-stream .ps1 here?
            probe = run(
                ps_command(
                    powershell,
                    f"$c = irm '{url}'; '{{0}}|len={{1}}|{{2}}' -f $c.GetType().Name, $c.Length, "
                    f"($c -is [string] -and $c.TrimStart([char]0xFEFF).StartsWith('# OrzMC'))",
                ),
                env=sandbox.env(),
            )
            say(f"  irm 探针: {plain(probe.stdout + probe.stderr).strip()} (exit={probe.returncode})")
            body = (
                f"& ([scriptblock]::Create((irm '{url}'))) -file '{BUILT_BINARY}' "
                f"-dir '{sandbox.oneline_dir}' -no-modify-rc"
            )
            result = run(ps_command(powershell, body), env={**sandbox.env_oneline(), "ORZMC_INSTALL_URL": url})
            _check_piped(result, sandbox.oneline_dir, sandbox.oneline_state_base, f"scriptblock({label})")
            if skip_download:
                say("  已跳过字面 `irm | iex`(--skip-download)")
            else:
                literal = run(
                    ps_command(powershell, "irm $env:ORZMC_INSTALL_URL | iex"),
                    env={**sandbox.env_iex(), "ORZMC_INSTALL_URL": url},
                )
                _check_piped(literal, sandbox.iex_dir, sandbox.iex_state_base, f"字面 {label}")
            return
        if not shutil.which("curl"):
            say("  已跳过(无 curl)")
            return
        url = f"{base}/install.sh"
        piped = f"curl -fsSL '{url}' | sh -s -- --file '{BUILT_BINARY}' --dir '{sandbox.oneline_dir}' --no-modify-rc"
        result = run(["sh", "-c", piped], env=sandbox.env_oneline())
    _check_piped(result, sandbox.oneline_dir, sandbox.oneline_state_base, label)


def _check_piped(result: subprocess.CompletedProcess[str], install_dir: Path, state_base: Path, label: str) -> None:
    """Shared assertions for every piped-install variant."""
    binary = install_dir / BINARY_NAME
    output = plain(result.stdout + result.stderr)
    last_line = output.strip().splitlines()[-1] if output.strip() else ""
    check(f"管道安装({label})成功", result.returncode == 0, output_of(result) if result.returncode else "exit=0")
    check(f"管道安装({label})后二进制存在且可执行", binary.is_file() and os.access(binary, os.X_OK))
    # Mojibake (PS 5.1 byte-decoding the source) would corrupt these characters.
    check(f"管道安装({label})中文未被误解码(编码自愈生效)", "已安装到" in output, last_line)
    check(f"管道安装({label})未改动 PATH 配置", "手动添加" not in output)
    record = state_base / "orzmc" / "install.conf"
    content = record.read_text(encoding="utf-8-sig") if record.is_file() else ""
    check(f"管道安装({label})写入独立安装记录", "version=" in content, f"{state_base.name}/orzmc/install.conf")


def step_uninstall(sandbox: Sandbox) -> None:
    say("== 10) 卸载闭环(self-uninstall) ==")
    result = sandbox.orzmc("self-uninstall", "--yes")
    check("self-uninstall 成功", result.returncode == 0, plain(result.stdout).strip())
    check("二进制已删除", not sandbox.binary.exists())
    check("安装记录已删除", not sandbox.manifest.exists())
    check("游戏数据根目录未因卸载被删", not sandbox.game_root.exists())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="orzmc 一键安装 + 自升级验收 harness(跨平台)")
    parser.add_argument("--skip-build", action="store_true", help="复用已有 dist/<binary>,不重新构建")
    parser.add_argument("--skip-download", action="store_true", help="跳过真实下载(离线环境)")
    parser.add_argument("--skip-oneline", action="store_true", help="跳过管道入口测试(irm | iex / curl | sh)")
    parser.add_argument(
        "--powershell",
        default="powershell",
        help="Windows 下运行 install.ps1 的 shell(默认 powershell=5.1;可传 pwsh=7.x)",
    )
    parser.add_argument(
        "--expect-ps-major", type=int, choices=(5, 7), default=None, help="要求 --powershell 的主版本(Windows)"
    )
    parser.add_argument("--keep", action="store_true", help="保留临时目录以便检查")
    parser.add_argument("--verbose", action="store_true", help="打印每条子命令")
    return parser


def main(argv: list[str] | None = None) -> int:
    global _verbose
    args = build_parser().parse_args(argv)
    _verbose = args.verbose
    force_utf8_stdio()
    sandbox = Sandbox(keep=args.keep)
    shell = args.powershell
    if not IS_WINDOWS and shell != "powershell":
        say(f"忽略 --powershell {shell}(仅在 Windows 生效)")
        shell = "powershell"
    flavour = _shell_label(shell) if IS_WINDOWS else "sh"
    say(f"平台: {sys.platform} {os.name} | 安装器 shell: {flavour} | 临时目录: {sandbox.root}")
    tag = latest_release_tag()
    say(f"最新 release tag: {tag or '(无法解析)'}")
    try:
        step_build(args.skip_build)
        step_shell_info(sandbox, shell, args.expect_ps_major)
        step_install(sandbox, shell)
        latest = step_check(sandbox, tag)
        step_swap_from_file(sandbox)
        step_download(sandbox, latest, args.skip_download, shell)
        step_bad_version(sandbox, shell)
        step_dev_guard(sandbox)
        step_oneline(sandbox, shell, args.skip_oneline, args.skip_download)
        step_uninstall(sandbox)
    finally:
        sandbox.remove()

    failures = [(name, detail) for name, passed, detail in _checks if not passed]
    say("\n==== 验收汇总 ====")
    say(f"  通过 {len(_checks) - len(failures)}/{len(_checks)}")
    for name, detail in failures:
        say(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
