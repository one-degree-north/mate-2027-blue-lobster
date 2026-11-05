"""Video sources that run GStreamer as a subprocess and read JPEG frames from its stdout.

    class Camera(GstSource):
        stream = "camera"

        def pipeline(self) -> str:
            return "videotestsrc ! jpegenc ! fdsink fd=1"

Python never links against GStreamer: ``gst-launch-1.0`` does the decoding and the JPEG
encoding, writes the frames back to back on stdout, and :class:`JpegSplitter` cuts them apart.
If the pipeline exits or stalls, the source restarts it and the UI shows a "NO SIGNAL" picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import shlex
from typing import ClassVar

from .source import Source
from .video import VideoStream

GST_LAUNCH = "gst-launch-1.0"
READ_SIZE = 64 * 1024
TERMINATE_GRACE = 2.0  # seconds to wait after SIGTERM before killing gst-launch
SOI = b"\xff\xd8"

# Inside entropy-coded data FF is followed by 00 (stuffing) or D0-D7 (restart); anything else
# after FF is a real marker.
_REAL_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
_INCOMPLETE = None
_CORRUPT = -1


def _jpeg_end(buf: bytearray) -> int | None:
    """Offset just past the JPEG that starts at ``buf[0]``; ``None`` if incomplete, -1 if invalid.

    Walks the marker segments instead of searching for the end-of-image bytes, because FFD9 can
    legitimately occur inside a header table.
    """
    i, n = 2, len(buf)
    while True:
        if i + 2 > n:
            return _INCOMPLETE
        if buf[i] != 0xFF:
            return _CORRUPT
        marker = buf[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
        elif marker == 0xD9:  # end of image
            return i + 2
        elif marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
            i += 2
        else:
            if i + 4 > n:
                return _INCOMPLETE
            length = (buf[i + 2] << 8) | buf[i + 3]
            if length < 2:
                return _CORRUPT
            i += 2 + length
            if marker == 0xDA:  # start of scan: compressed data runs until the next real marker
                found = _REAL_MARKER.search(buf, i)
                if found is None:
                    return _INCOMPLETE
                i = found.start()


class JpegSplitter:
    """Cuts a byte stream of back-to-back JPEGs into single frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Add bytes; return every frame that is now complete. Garbage between frames is dropped."""
        self._buf += data
        frames: list[bytes] = []
        while True:
            start = self._buf.find(SOI)
            if start < 0:
                keep = 1 if self._buf.endswith(b"\xff") else 0  # could be half a marker
                del self._buf[: len(self._buf) - keep]
                return frames
            del self._buf[:start]
            end = _jpeg_end(self._buf)
            if end is _INCOMPLETE:
                return frames
            if end == _CORRUPT:
                del self._buf[:2]  # skip this start marker and look for the next frame
                continue
            frames.append(bytes(self._buf[:end]))
            del self._buf[:end]


def no_signal_jpeg(width: int = 640, height: int = 360) -> bytes:
    """The picture shown while a source delivers nothing."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), (24, 28, 36))
    draw = ImageDraw.Draw(image)
    draw.text(
        (width / 2, height / 2),
        "NO SIGNAL",
        fill=(150, 160, 175),
        font=ImageFont.load_default(size=height // 8),
        anchor="mm",
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


class GstSource(Source):
    """A source that plays a ``gst-launch-1.0`` pipeline ending in JPEG frames on stdout."""

    stream: ClassVar[str]
    """Name of the video stream this source publishes."""

    launcher: ClassVar[tuple[str, ...]] = (GST_LAUNCH, "-q")
    no_signal_after: ClassVar[float] = 1.0
    """Seconds without a frame before the UI is shown "NO SIGNAL"."""
    restart_delay: ClassVar[float] = 1.0

    def pipeline(self) -> str:
        """The pipeline description; it must end with ``... ! jpegenc ! fdsink fd=1``."""
        raise NotImplementedError

    def problem(self) -> str | None:
        """Return a message if the source cannot start (e.g. a missing file), else ``None``."""
        return None

    async def run(self) -> None:
        stream = self.video(self.stream)
        placeholder = no_signal_jpeg()
        while True:
            if (message := self.problem()) is not None:
                self.log.error("%s", message)
                self.report("error", detail=message)
                await asyncio.sleep(max(self.restart_delay, 5.0))
                continue
            self.report("starting")
            try:
                code = await self._play(stream, placeholder)
            except FileNotFoundError:
                message = (
                    f"{self.launcher[0]} not found: install GStreamer (brew install gstreamer)"
                )
                self.log.error("%s", message)
                self.report("error", detail=message)
                await asyncio.sleep(max(self.restart_delay, 5.0))
                continue
            self.log.warning("pipeline exited with code %s; restarting", code)
            self.report("stopped", code=code)
            await asyncio.sleep(self.restart_delay)

    async def _play(self, stream: VideoStream, placeholder: bytes) -> int | None:
        proc = await asyncio.create_subprocess_exec(
            *self.launcher,
            *shlex.split(self.pipeline()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        errors = asyncio.create_task(self._forward_errors(proc))
        try:
            await self._read_frames(proc, stream, placeholder)
        finally:
            await _terminate(proc)
            await errors
        return proc.returncode

    async def _read_frames(
        self, proc: asyncio.subprocess.Process, stream: VideoStream, placeholder: bytes
    ) -> None:
        assert proc.stdout is not None
        loop = asyncio.get_running_loop()
        splitter = JpegSplitter()
        state = "starting"
        count, window_start = 0, loop.time()
        while True:
            try:
                async with asyncio.timeout(self.no_signal_after):
                    chunk = await proc.stdout.read(READ_SIZE)
            except TimeoutError:
                stream.push_jpeg(placeholder)
                if state != "no signal":
                    state = "no signal"
                    self.report(state)
                count, window_start = 0, loop.time()
                continue
            if not chunk:
                return  # the pipeline ended
            for jpeg in splitter.feed(chunk):
                stream.push_jpeg(jpeg)
                count += 1
            now = loop.time()
            if now - window_start >= 1.0:
                state = "live"
                self.report(state, fps=round(count / (now - window_start), 1))
                count, window_start = 0, now

    async def _forward_errors(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            if line := raw.decode(errors="replace").rstrip():
                self.log.warning("gst: %s", line)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), TERMINATE_GRACE)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
