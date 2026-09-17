"""Filesystem helpers with a thin, testable surface."""

from __future__ import annotations

import json
import os
import shutil
from typing import Any


class FileStore:
    """All direct filesystem access in the library goes through here."""

    def ensure_dir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def list_dir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return []

    def file_size(self, path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return -1

    def mtime(self, path: str) -> float:
        """Last-modification time in epoch seconds; 0.0 when unavailable."""
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        with open(path, encoding=encoding) as f:
            return f.read()

    def read_json(self, path: str) -> Any:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding=encoding) as f:
            f.write(content)

    def write_json(self, path: str, obj: Any) -> None:
        self.write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))

    def remove(self, path: str) -> None:
        """Remove a file or directory tree; ignore when missing."""
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def move(self, src: str, dst: str) -> None:
        self.ensure_dir(os.path.dirname(dst))
        shutil.move(src, dst)

    def ensure_symlink(self, link: str, target: str) -> None:
        """Create/replace a symlink at ``link`` pointing to ``target``."""
        self.ensure_dir(os.path.dirname(link))
        if os.path.islink(link):
            os.remove(link)
        elif os.path.exists(link):
            raise FileExistsError(f"路径已存在且不是符号链接: {link}")
        os.symlink(target, link)
