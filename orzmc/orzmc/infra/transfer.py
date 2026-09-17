"""Byte-progress file transfer shared by every download path.

``Downloader``, ``JavaEnv`` and the Mojang metadata download all funnel through
this helper, so byte progress is rendered identically everywhere.
"""

from __future__ import annotations

from orzmc.infra.http import HttpClient
from orzmc.infra.progress import ProgressSink


def download_with_progress(
    http: HttpClient,
    url: str,
    dest: str,
    sink: ProgressSink,
    desc: str,
    timeout: float | tuple[float, float] | None = None,
) -> int:
    """Stream ``url`` to ``dest``, reporting byte progress to ``sink``.

    Returns the number of bytes written. ``total`` is unknown when the server
    omits ``Content-Length`` — the progress line then shows a pulsing bar
    instead of a percentage, which still tells the user work is happening.

    The size comes from the streaming GET response, so no HEAD probe is issued:
    on a high-latency link that probe costs a whole extra round trip per file.
    """
    try:
        return http.download(
            url,
            dest,
            on_chunk=lambda n: sink.advance(n),
            on_open=lambda total: sink.start(desc, total),
            timeout=timeout,
        )
    finally:
        sink.finish()
