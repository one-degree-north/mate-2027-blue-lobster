"""Module-side video: push JPEG frames to the hub, which serves them to the UI as MJPEG.

Frames bypass the JSON message bus. Only the latest frame is kept, so a slow
network or browser costs frames, never latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import re
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)

STREAM_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
JPEG_MAGIC = b"\xff\xd8"

DEFAULT_SOURCE = os.environ.get("ROVKIT_SOURCE", "camera")
"""Stream that ``Module.frames()`` reads when no name is given. Set ``ROVKIT_SOURCE`` to change."""


def ingest_url(hub_url: str, stream: str) -> str:
    parts = urlsplit(hub_url)
    return f"{parts.scheme}://{parts.netloc}/video/{stream}/ingest"


def frames_url(hub_url: str, stream: str) -> str:
    parts = urlsplit(hub_url)
    return f"{parts.scheme}://{parts.netloc}/video/{stream}/frames"


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
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self._url = url
        self._session = session
        self._loop = loop
        self._on_close = on_close
        self._task: asyncio.Task[None] | None = None
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
        self._task = self._loop.create_task(self.run(), name=f"video-{self.name}")
        return self._task

    @staticmethod
    async def stop(task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def close(self) -> None:
        """Stop sending and take the stream off the UI's list. Safe to call twice."""
        if self._task is not None:
            task, self._task = self._task, None
            await self.stop(task)
        if self._on_close is not None:
            on_close, self._on_close = self._on_close, None
            on_close()


class FrameSubscription:
    """Receives the newest frames of a stream; created with :meth:`Module.frames`.

        async with self.frames("camera") as frames:
            async for jpeg in frames:
                ...

    Only the latest frame is kept: a consumer that is busy skips frames instead of
    falling behind. Leaving the ``async with`` block closes the link to the hub.
    """

    def __init__(self, name: str, url: str, session: aiohttp.ClientSession) -> None:
        self.name = name
        self._url = url
        self._session = session
        self._task: asyncio.Task[None] | None = None
        self._latest: bytes | None = None
        self._fresh = asyncio.Event()

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._receive(), name=f"frames-{self.name}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def next(self, timeout: float | None = None) -> bytes:  # noqa: ASYNC109 - same shape as asyncio.wait_for
        """The newest JPEG not yet returned. Raises ``TimeoutError`` if none arrives in time."""
        if self._task is None:
            raise RuntimeError("use FrameSubscription inside 'async with'")
        await asyncio.wait_for(self._fresh.wait(), timeout)
        self._fresh.clear()
        assert self._latest is not None
        return self._latest

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        return await self.next()

    async def _receive(self) -> None:
        delay = 0.25
        while True:
            try:
                async with self._session.ws_connect(self._url, heartbeat=15, max_msg_size=0) as ws:
                    delay = 0.25
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            self._latest = message.data
                            self._fresh.set()
            except (aiohttp.ClientError, OSError, ConnectionError) as exc:
                log.warning("frame link to %r lost (%s); retrying", self.name, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)
