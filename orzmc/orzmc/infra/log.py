"""Reporter protocol + rich default implementation.

The library only depends on the ``Reporter`` protocol; the app layer can inject
its own implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from rich.console import Console
from rich.theme import Theme

ORZMC_THEME = Theme(
    {
        "info": "cyan",
        "warn": "yellow",
        "error": "bold red",
        "success": "green",
        "prompt": "bold magenta",
        "muted": "dim",
    }
)


class Reporter(ABC):
    """Semantic log output. Implementations decide how text is rendered."""

    @abstractmethod
    def debug(self, text: str) -> None: ...

    @abstractmethod
    def info(self, text: str) -> None: ...

    @abstractmethod
    def warn(self, text: str) -> None: ...

    @abstractmethod
    def error(self, text: str) -> None: ...

    @abstractmethod
    def success(self, text: str) -> None: ...

    @abstractmethod
    def plain(self, text: str) -> None: ...


class NullReporter(Reporter):
    """Discards all output."""

    def debug(self, text: str) -> None: ...

    def info(self, text: str) -> None: ...

    def warn(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...

    def success(self, text: str) -> None: ...

    def plain(self, text: str) -> None: ...


class RichReporter(Reporter):
    """Renders to a rich Console. ``debug`` lines only show when ``verbose``.

    An injected ``console`` (the CLI passes ``Console(stderr=True)`` for
    ``--json``) may not carry :data:`ORZMC_THEME`, and ``print(..., style="info")``
    then raises ``MissingStyle`` on the very first log line. Push the theme onto
    it so injected consoles behave like the default one.
    """

    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        if console is None:
            self._console = Console(theme=ORZMC_THEME)
        else:
            self._console = console
            self._console.push_theme(ORZMC_THEME)
        self.verbose = verbose

    def _print(self, text: str, style: str) -> None:
        self._console.print(text, style=style)

    def debug(self, text: str) -> None:
        if self.verbose:
            self._print(text, "muted")

    def info(self, text: str) -> None:
        self._print(text, "info")

    def warn(self, text: str) -> None:
        self._print(text, "warn")

    def error(self, text: str) -> None:
        self._print(text, "error")

    def success(self, text: str) -> None:
        self._print(text, "success")

    def plain(self, text: str) -> None:
        self._console.print(text)
