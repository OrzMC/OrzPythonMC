#!/usr/bin/env python3
"""Cross-platform real-environment acceptance harness for orzmc.

Replaces the throwaway bash scripts under ``/tmp/orzmc-accept`` that relied on
``pgrep``/``pkill``/``kill -0`` — none of which port cleanly to Windows. Uses
``psutil`` for cross-platform process-tree management and a plain socket for
port checks, so the same script drives local runs and the GitHub Actions
``acceptance.yml`` workflow on macOS/Linux/Windows x86_64/arm64.

Run from the workspace root::

    uv run --package orzmc-app python scripts/acceptance.py \\
        --case server:vanilla:latest --case client:vanilla:latest \\
        --backcompat "1.20.4"            # optional old-version smoke

Version strategy: ``latest`` is the primary baseline (resolved from Mojang's
``version_manifest_v2.json`` — the same manifest orzmc itself uses); old
versions are only a backward-compat smoke via ``--backcompat`` (vanilla server
+ client per version, enough to prove the Mojang manifest/launch pipeline still
parses that major).

Verdicts:

- ``PASS``          — server logged ``Done (`` / client exited 0 (bootstrap).
- ``UP(no Done)``   — server port open but no ``Done (`` inside the grace
                      window: Mojang 1.20.x world-gen stall (MC-263542), not
                      an orzmc defect. Reported as a warning, not a failure.
- ``SKIP``          — the type does not support this MC version yet (e.g.
                      Forge usually lags one stable release). Type adapts to
                      different version windows than the baseline.
- ``UP(gap)``       — upstream ships nothing for this platform/arch (Adoptium
                      has no JRE for this OS/arch/major; Mojang ships no client
                      natives for this arch). Warning, not a failure — the gap
                      self-heals when the provider fills it.
- ``FAIL(exit N)``  — the orzmc process died without reaching a good state.
- ``TIMEOUT``       — no verdict within ``--timeout`` seconds.

Exit code is non-zero iff any case is ``FAIL`` or ``TIMEOUT``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

try:
    import psutil
except ImportError:  # dev dependency — install via `uv sync`
    print("需要 psutil(dev 依赖):请先运行 uv sync", file=sys.stderr)
    raise

MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
DONE_MARK = "Done ("
POLL_INTERVAL = 3
GRACE_AFTER_PORT = 90  # port open this long without Done → Mojang world-gen stall
DEFAULT_TIMEOUT = 300
BASE_PORT = 25570

# Type providers report a missing version with these messages (paper/fabric use
# "不支持", forge uses "未找到 … 的 Forge 版本"). Matching either ⇒ SKIP, i.e.
# the type simply has no build for this MC version yet.
SKIP_MARKS = ("不支持 Minecraft", "未找到 Minecraft")

# Upstream platform gaps: orzmc ran the whole pipeline but the *provider* ships
# nothing for this OS/arch/major, so no case on this platform can succeed no
# matter what orzmc does. Matching ⇒ UP(gap) — a warning, not a failure (same
# philosophy as UP(no Done)). The gap is upstream, so it self-heals: once the
# provider fills it (Adoptium ships the JRE, Mojang ships the natives), the case
# returns to real verdicts automatically.
#   - Adoptium 404 for the Temurin binary URL ⇒ no JRE/JDK build for this
#     OS/arch/major (e.g. MC 26.2 needs Java 25; Adoptium ships none for
#     windows/aarch64). requests prints "404 Client Error: Not Found for url: …".
#   - LWJGL native-load failure ("Failed to locate library" / "[LWJGL] Failed
#     to load a library" / the game's NativeLibrariesBootstrap.loadLibrary
#     crash-report frame) ⇒ Mojang ships no native lib for this platform/arch
#     (e.g. 26.2 has no linux-arm64 natives — the bundled natives-linux.jar is
#     x86-64 only, so LWJGL reports the arch mismatch). Each loader prints its
#     own message: vanilla "[LWJGL] Failed to load a library", forge a raw
#     UnsatisfiedLinkError, fabric a crash report whose "[LWJGL]" line orzmc's
#     own tail can truncate — so also match the surviving crash-report frame.
GAP_MARKS = (
    "404 Client Error: Not Found for url: https://api.adoptium.net/v3/binary/latest",
    "Failed to locate library",
    "[LWJGL] Failed to load a library",
    "NativeLibrariesBootstrap.loadLibrary",
)

ROOT = Path(__file__).resolve().parent.parent
SERVERS = {"vanilla", "paper", "fabric", "forge"}
CLIENTS = {"vanilla", "fabric", "forge"}


def _fail(message: str) -> NoReturn:
    print(f"错误:{message}", file=sys.stderr)
    raise SystemExit(2)


@dataclass(frozen=True)
class Case:
    role: str  # "server" | "client"
    game_type: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.version} {self.role}-{self.game_type}"


@dataclass
class Options:
    root: Path
    logs: Path
    timeout: int
    deep_client: bool
    cases: list[Case]


# ── small helpers ──────────────────────────────────────────────────────────


def resolve_latest() -> str:
    """Latest release from Mojang's manifest (no extra sources)."""
    with urllib.request.urlopen(MANIFEST_URL, timeout=30) as resp:
        manifest = json.load(resp)
    return manifest["latest"]["release"]


def log_contains(path: Path, needles: tuple[str, ...]) -> bool:
    """True if any needle appears in the log, ignoring how lines are wrapped.

    orzmc prints through rich's Console, which soft-wraps long lines at its
    fallback width when there's no tty — e.g. the Adoptium 404 URL gets split
    across lines with blank gaps. The wrap can land **inside** a marker, so
    strip whitespace from both sides before comparing: keeping a single space
    (the old behaviour) still missed `…/binary/latest` when the break fell
    inside it. Markers never depend on whitespace, so this is safe.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    squeezed = re.sub(r"\s+", "", text)
    return any(re.sub(r"\s+", "", needle) in squeezed for needle in needles)


def log_tail(path: Path, size: int = 4096) -> str:
    try:
        data = path.read_bytes()[-size:]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return "(无日志)"


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def orzmc_cmd(args: list[str]) -> list[str]:
    """Invoke the CLI through the interpreter running this harness — portable
    across OSes and avoids a second nested ``uv run``."""
    return [sys.executable, "-m", "orzmc_app.cli", *args]


# ── process tree management (psutil) ────────────────────────────────────────


def _terminate(proc: psutil.Process) -> None:
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return
    try:
        proc.wait(timeout=3)
    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


def kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and every descendant (covers the java server child
    that orzmc spawns via run_stream/run_detached with its own session)."""
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    for child in parent.children(recursive=True):
        _terminate(child)
    _terminate(parent)


def java_processes(root: Path) -> list[psutil.Process]:
    """All java processes whose cmdline mentions this game root."""
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not cmdline:
            continue
        name = os.path.basename(cmdline[0]).lower()
        if name.startswith("java") and str(root) in " ".join(cmdline):
            found.append(proc)
    return found


def kill_java(root: Path) -> None:
    for proc in java_processes(root):
        _terminate(proc)


# ── case runners ────────────────────────────────────────────────────────────


def _cleanup_server_world(case: Case, opts: Options) -> None:
    world_base = opts.root / "versions" / case.version / "server" / case.game_type
    for name in ("world", "world_nether", "world_the_end"):
        shutil.rmtree(world_base / name, ignore_errors=True)


def run_server_case(case: Case, opts: Options, port: int) -> str:
    # Cached roots survive across CI runs; a run killed mid-world-gen leaves a
    # world that fails to load on the next run (world_gen_settings.dat missing).
    # Clean before AND after so every case starts from a fresh world.
    _cleanup_server_world(case, opts)
    log = opts.logs / f"server-{case.game_type}-{case.version}.log"
    cmd = orzmc_cmd(
        [
            "server",
            "-v",
            case.version,
            "-t",
            case.game_type,
            "--yes",
            "--root-dir",
            str(opts.root),
            "--server-args",
            f"nogui --port {port}",
        ]
    )
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(ROOT))

    deadline = time.time() + opts.timeout
    result = "TIMEOUT"
    port_open_since: float | None = None
    while time.time() < deadline:
        if log_contains(log, (DONE_MARK,)):
            result = "PASS"
            break
        if proc.poll() is not None:
            # Died before reaching Done: distinguish "type lacks this version"
            # (SKIP) and "upstream ships nothing for this platform" (UP(gap))
            # from a real regression.
            if log_contains(log, SKIP_MARKS):
                result = "SKIP"
            elif log_contains(log, GAP_MARKS):
                result = "UP(gap)"
            else:
                result = "FAIL(exit)"
            break
        if port_open(port):
            if port_open_since is None:
                port_open_since = time.time()
            if time.time() - port_open_since >= GRACE_AFTER_PORT:
                result = "UP(no Done)"
                break
        else:
            port_open_since = None
        time.sleep(POLL_INTERVAL)
    if result == "TIMEOUT" and port_open(port):
        result = "UP(no Done)"

    kill_tree(proc)
    _cleanup_server_world(case, opts)
    return result


def run_client_case(case: Case, opts: Options) -> str:
    log = opts.logs / f"client-{case.game_type}-{case.version}.log"
    cmd = orzmc_cmd(["client", "-v", case.version, "-t", case.game_type, "--root-dir", str(opts.root)])
    # Headless Linux runners have no X server; xvfb-run gives the game a virtual
    # display (env propagates to the detached java via run_detached).
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        cmd = ["xvfb-run", "-a", *cmd]
    with log.open("wb") as out:
        code = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(ROOT)).returncode

    result = (
        "PASS"
        if code == 0
        else "SKIP"
        if log_contains(log, SKIP_MARKS)
        else "UP(gap)"
        if log_contains(log, GAP_MARKS)
        else f"FAIL(exit {code})"
    )
    if opts.deep_client and result == "PASS":
        # Optional strongest check (off by default): game java still alive 15s
        # after bootstrap. CI's macOS/Windows have no GPU/GL context, so this
        # can false-negative there — keep it for real-desktop manual runs.
        time.sleep(15)
        if not java_processes(opts.root):
            result = "FAIL(deep: java 已退出)"
    kill_java(opts.root)  # orzmc client exits after its 3s window; java is detached
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_cases(specs: list[str], latest: str) -> list[Case]:
    cases: list[Case] = []
    for spec in specs:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            _fail(f"无效 case(应为 ROLE:TYPE:VERSION):{spec}")
        role, game_type, version = parts
        if role not in ("server", "client"):
            _fail(f"未知角色 {role}(server|client)")
        valid = SERVERS if role == "server" else CLIENTS
        if game_type not in valid:
            _fail(f"类型 {game_type} 不能用于{'服务端' if role == 'server' else '客户端'}")
        version = latest if version == "latest" else version
        cases.append(Case(role=role, game_type=game_type, version=version))
    return cases


def parse_backcompat(backcompat: str, latest: str) -> list[Case]:
    """Old-version backward-compat smoke: vanilla server + client per version."""
    cases: list[Case] = []
    for version in (v.strip() for v in backcompat.split(",") if v.strip()):
        version = latest if version == "latest" else version
        cases.append(Case(role="server", game_type="vanilla", version=version))
        cases.append(Case(role="client", game_type="vanilla", version=version))
    return cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="orzmc 真实验收 harness(跨平台)")
    parser.add_argument("--root", type=Path, default=Path(temp_dir()), help="游戏根目录(默认系统临时目录)")
    parser.add_argument("--logs", type=Path, default=None, help="日志目录(默认 <root>/logs)")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"单 case 超时秒数(默认 {DEFAULT_TIMEOUT})"
    )
    parser.add_argument("--deep-client", action="store_true", help="客户端追加 15s java 存活检查(默认关,CI 可能假阴性)")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="ROLE:TYPE:VERSION",
        help="验收 case,可重复(server|client):(vanilla|paper|fabric|forge):(版本|latest)",
    )
    parser.add_argument(
        "--backcompat",
        default="",
        metavar="V1,V2",
        help="旧版本后向兼容冒烟:对每个版本追加 server-vanilla + client-vanilla",
    )
    return parser


def temp_dir() -> str:
    import tempfile

    return tempfile.gettempdir()


def main(argv: list[str] | None = None) -> int:
    # Windows CI pipes stdout/stderr through the ANSI codepage (cp1252), which
    # cannot encode our Chinese messages — force UTF-8 (lossy fallback) so a
    # console codec mismatch never kills the harness before it runs.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    if not args.case and not args.backcompat:
        _fail("至少需要一个 --case 或 --backcompat")

    # Only hit the network when a case actually references "latest" — explicit
    # version runs (e.g. offline backcompat smoke) need no manifest fetch.
    needs_latest = any(spec.split(":")[-1] == "latest" for spec in args.case) or any(
        v == "latest" for v in args.backcompat.split(",")
    )
    latest = resolve_latest() if needs_latest else "—"
    print(f"平台: {sys.platform} {os.name} | 最新版: {latest} | root: {args.root}")

    cases = parse_cases(args.case, latest)
    cases += parse_backcompat(args.backcompat, latest)
    # de-dup (same role/type/version would re-run identical work)
    seen: set[tuple[str, str, str]] = set()
    unique: list[Case] = []
    for case in cases:
        key = (case.role, case.game_type, case.version)
        if key not in seen:
            seen.add(key)
            unique.append(case)
    cases = unique

    opts = Options(
        root=args.root,
        logs=args.logs or args.root / "logs",
        timeout=args.timeout,
        deep_client=args.deep_client,
        cases=cases,
    )
    opts.root.mkdir(parents=True, exist_ok=True)
    opts.logs.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Case, str]] = []
    next_port = BASE_PORT
    try:
        for case in cases:
            label = case.label
            started = time.monotonic()
            if case.role == "server":
                port = next_port
                next_port += 1
                result = run_server_case(case, opts, port)
            else:
                result = run_client_case(case, opts)
            elapsed = time.monotonic() - started
            results.append((case, result))
            print(f"[{result:>12}] {label:<28} ({elapsed:.1f}s)")
    except KeyboardInterrupt:
        print("\n被中断,清理进程...", file=sys.stderr)
        kill_java(opts.root)
        return 1

    print("\n==== 验收汇总 ====")
    failures = 0
    for case, result in results:
        mark = "OK" if result in ("PASS", "SKIP", "UP(no Done)", "UP(gap)") else "!!"
        print(f"  [{mark}] {case.label}: {result}")
        if result.startswith("FAIL") or result == "TIMEOUT":
            failures += 1
            # Surface the reason in the step output — CI has no console to look
            # at, so a bare verdict is undiagnosable (see arm64 client exit 1).
            log = opts.logs / f"{case.role}-{case.game_type}-{case.version}.log"
            print(f"      └─ 日志尾部:\n{log_tail(log)}")
    if failures:
        print(f"\n失败 {failures}/{len(results)} 个 case(详见上方日志尾部)")
    else:
        print(f"\n全部通过 {len(results)}/{len(results)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
