"""Module-side video: push JPEG frames to the hub, which serves them to the UI as MJPEG.

Frames bypass the JSON message bus. Only the latest frame is kept, so a slow
network or browser costs frames, never latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)

STREAM_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
JPEG_MAGIC = b"\xff\xd8"


def ingest_url(hub_url: str, stream: str) -> str:
    parts = urlsplit(hub_url)
    return f"{parts.scheme}://{parts.netloc}/video/{stream}/ingest"


def encode_jpeg(image: Any, quality: int = 80) -> bytes:
    """Encode a PIL image, or an RGB ``numpy`` array, as JPEG.

    OpenCV frames are BGR: use ``cv2.imencode('.jpg', frame)[1].tobytes()`` instead.
    """
    from PIL import Image

    if not isinstance(image, Image.Image):
        image = Image.fromarray(image)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


class VideoStream:
    """Created with :meth:`Module.video`; call :meth:`push_jpeg` from any thread."""

    def __init__(
        self,
        name: str,
        url: str,
        session: aiohttp.ClientSession,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.name = name
        self._url = url
        self._session = session
        self._loop = loop
        self._latest: bytes | None = None
        self._ready = asyncio.Event()

    def push_jpeg(self, data: bytes) -> None:
        """Publish a frame. Thread-safe; a newer frame replaces one not yet sent."""
        if not data.startswith(JPEG_MAGIC):
            raise ValueError("push_jpeg needs JPEG data (see rovkit.video.encode_jpeg)")
        self._latest = data
        self._loop.call_soon_threadsafe(self._ready.set)

    async def run(self) -> None:
        delay = 0.25
        while True:
            try:
                async with self._session.ws_connect(self._url, max_msg_size=0) as ws:
                    delay = 0.25
                    while True:
                        await self._ready.wait()
                        self._ready.clear()
                        if self._latest is not None:
                            await ws.send_bytes(self._latest)
            except (aiohttp.ClientError, OSError, ConnectionError) as exc:
                log.warning("video stream %r disconnected (%s); retrying", self.name, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    def start(self) -> asyncio.Task[None]:
        return self._loop.create_task(self.run(), name=f"video-{self.name}")

    @staticmethod
    async def stop(task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
