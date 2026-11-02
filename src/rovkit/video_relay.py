"""Hub-side video relay: modules push JPEG frames in, browsers pull MJPEG out."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

from .video import JPEG_MAGIC, STREAM_NAME_PATTERN

log = logging.getLogger(__name__)

BOUNDARY = "rovkitframe"
MAX_FRAME_BYTES = 8 * 1024 * 1024


@dataclass
class _Stream:
    frame: bytes | None = None
    seq: int = 0
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def publish(self, frame: bytes) -> None:
        async with self.changed:
            self.frame, self.seq = frame, self.seq + 1
            self.changed.notify_all()

    async def next_after(self, seq: int) -> tuple[bytes, int]:
        """Wait for a frame newer than ``seq``. Skipped frames are intentionally lost."""
        async with self.changed:
            await self.changed.wait_for(lambda: self.seq > seq)
            assert self.frame is not None
            return self.frame, self.seq


class VideoRelay:
    def __init__(self) -> None:
        self._streams: dict[str, _Stream] = {}

    def _stream(self, request: web.Request) -> _Stream:
        name = request.match_info["name"]
        if not STREAM_NAME_PATTERN.match(name):
            raise web.HTTPBadRequest(reason="invalid stream name")
        return self._streams.setdefault(name, _Stream())

    async def ingest(self, request: web.Request) -> web.StreamResponse:
        stream = self._stream(request)
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=MAX_FRAME_BYTES)
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.BINARY and msg.data.startswith(JPEG_MAGIC):
                await stream.publish(msg.data)
            elif msg.type != WSMsgType.CLOSE:
                log.warning("ignoring non-JPEG data on video ingest")
        return ws

    async def view(self, request: web.Request) -> web.StreamResponse:
        stream = self._stream(request)
        response = web.StreamResponse(
            headers={
                "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                "Cache-Control": "no-store",
            }
        )
        await response.prepare(request)
        seq = 0 if stream.frame is None else stream.seq - 1  # start with the current frame
        try:
            while True:
                frame, seq = await stream.next_after(seq)
                head = (
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                )
                await response.write(head.encode() + frame + b"\r\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # viewer went away or the hub is shutting down
        return response
