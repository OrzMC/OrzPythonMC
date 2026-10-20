"""File hashing helpers."""

from __future__ import annotations

import hashlib


def sha1_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """SHA-1 hex digest of a file (streamed)."""
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 hex digest of a file (streamed); 用于运行时通道校验资产。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
