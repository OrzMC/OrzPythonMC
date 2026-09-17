"""OrzMC application: typer CLI, built on the orzmc library.

The app layer only calls the library's public API (``orzmc/__init__.py``).
"""

from __future__ import annotations

__version__ = "2.1.0"  # x-release-please-version(与库版本锁步)

__all__ = ["__version__"]
