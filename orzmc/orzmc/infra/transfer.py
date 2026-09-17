"""Byte-progress file transfer shared by every download path.

``Downloader``, ``JavaEnv`` and the Mojang metadata download all funnel through
this helper, so byte progress is rendered identically everywhere.
"""

from __future__ import annotations

from orzmc.infra.http import HttpClient
from orzmc.infra.progress import ProgressSink


def download_with_progress(http: HttpClient, url: str, dest: str, sink: ProgressSink, desc: str) -> int:
    """Stream ``url`` to ``dest``, reporting byte progress to ``sink``.

    Returns the number of bytes written. ``total`` is unknown when the server
    omits ``Content-Length`` — the progress line then shows a pulsing bar
    instead of a percentage, which still tells the user work is happening.
    """
    total = http.content_length(url)
    sink.start(desc, total)
    try:
        return http.download(url, dest, on_chunk=lambda n: sink.advance(n))
    finally:
        sink.finish()
