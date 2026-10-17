"""Build the orzmc-app into a single-file PyInstaller binary → dist/.

Run from the workspace root: `uv run --package orzmc-app python scripts/build.py`

``--name`` gives the binary a per-platform base name so a multi-platform
release attaches distinct assets (``orzmc-linux-x86_64`` etc.) instead of
every platform overwriting the same ``orzmc`` file on the Release.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _check_python() -> None:
    """Abort on macOS framework Pythons so a slow binary can never be built.

    PyInstaller on a framework-layout Python (python.org / Homebrew, base_prefix
    contains ``Python.framework``) bundles the whole 8MB framework dylib and the
    frozen binary dlopens it at startup — ~14s instead of ~1.5s for the uv
    standalone (embed) layout. uv's ``python-preference = "only-managed"`` keeps
    every build on the standalone interpreter; this guard makes any regression a
    loud build failure instead of a silently slow release.
    """
    if sys.platform != "darwin":
        return
    if "Python.framework" in sys.base_prefix:
        raise SystemExit(
            "build.py: 当前解释器是 macOS framework Python "
            f"({sys.base_prefix});PyInstaller 会打包 Python.framework,"
            "frozen 启动需 ~14s。请用非 framework(uv standalone)Python 构建:"
            '确保 pyproject.toml [tool.uv] python-preference = "only-managed" 生效。'
        )


def main() -> int:
    _check_python()
    parser = argparse.ArgumentParser(description="Build the orzmc-app single-file binary into dist/.")
    parser.add_argument("--name", default="orzmc", help="Binary base name (e.g. orzmc-linux-arm64); default: orzmc")
    args = parser.parse_args()
    name = args.name

    dist = ROOT / "dist"
    build_dir = ROOT / ".pybuild"
    for d in (dist, build_dir):
        if d.exists():
            shutil.rmtree(d)
    dist.mkdir(parents=True, exist_ok=True)

    # Entry point: orzmc_app/orzmc_app/cli/__main__.py (package layout)
    entry = ROOT / "orzmc_app" / "orzmc_app" / "cli" / "__main__.py"
    if not entry.exists():
        print(f"Entry point not found: {entry}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--clean",
        "--noconfirm",
        # shellingham 用 ``importlib.import_module(f".{os.name}")`` **动态**导入平台
        # 实现,PyInstaller 静态分析看不到,也没有对应 hook → 冻结后的二进制里缺
        # ``shellingham.posix`` / ``shellingham.nt``,任何 shell 探测都直接
        # ``RuntimeError: Shell detection not implemented for 'posix'``
        # (2.11.0 及更早的发布版真踩过:``--show-completion`` / ``completion install``)。
        # 现在 CLI 自己不再依赖探测,但 typer 的内部补全路径仍可能在引用它 ——
        # 显式收集比“祈祷用不到”可靠,也避免下一个调用点重踩。
        "--collect-submodules",
        "shellingham",
        str(entry),
    ]
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("PyInstaller build failed", file=sys.stderr)
        return result.returncode

    binary = dist / (name + (".exe" if sys.platform == "win32" else ""))
    print(f"Binary: {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
