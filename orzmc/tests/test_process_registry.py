"""Process registry: PID records for ``orzmc server --kill-all`` (offline, real child)."""

from __future__ import annotations

import os
import subprocess
import sys

from orzmc.infra.fs import FileStore
from orzmc.infra.process_registry import ProcessRegistry


def _registry(tmp_path) -> ProcessRegistry:
    return ProcessRegistry(FileStore(), os.path.join(str(tmp_path), "run"))


def test_register_records_unregister(tmp_path) -> None:
    registry = _registry(tmp_path)
    path = registry.register(1234, "server-1.20.4-vanilla")
    assert os.path.isfile(path)
    assert registry.records() == [(path, 1234)]
    registry.unregister(path)
    assert registry.records() == []


def test_records_skips_unreadable(tmp_path) -> None:
    registry = _registry(tmp_path)
    directory = os.path.join(str(tmp_path), "run")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "broken.pid"), "w") as handle:
        handle.write("not-a-pid\n")
    assert registry.records() == []


def test_kill_all_terminates_and_clears(tmp_path) -> None:
    registry = _registry(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        registry.register(child.pid, "server-x")
        killed = registry.kill_all()
        assert child.pid in killed
        assert registry.records() == []
        child.wait(timeout=10)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_kill_all_clears_stale(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.register(2**30, "stale")  # almost certainly no such process
    registry.kill_all()
    assert registry.records() == []
