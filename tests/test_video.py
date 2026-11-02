from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from PIL import Image

from rovkit.client import HubClient
from rovkit.hub import create_app
from rovkit.video import VideoStream, encode_jpeg, ingest_url

from .conftest import free_port


def jpeg(colour: tuple[int, int, int]) -> bytes:
    return encode_jpeg(Image.new("RGB", (16, 16), colour))


@pytest.fixture
async def base() -> AsyncIterator[str]:
    port = free_port()
    runner = web.AppRunner(create_app(), handle_signals=False, shutdown_timeout=0.1)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    yield f"127.0.0.1:{port}"
    await runner.cleanup()


def dominant_channel(data: bytes) -> int:
    """Index (0=R, 1=G, 2=B) of the strongest channel in the centre of a JPEG."""
    pixel = Image.open(io.BytesIO(data)).convert("RGB").getpixel((8, 8))
    assert isinstance(pixel, tuple)
    return max(range(3), key=lambda i: pixel[i])


async def read_part(response: aiohttp.ClientResponse) -> bytes:
    """Read one multipart part from an MJPEG response and return the JPEG bytes."""
    await response.content.readuntil(b"\r\n\r\n")  # boundary + part headers
    # Content-Length is in the headers we just skipped over; JPEGs end with FFD9 + CRLF.
    return await response.content.readuntil(b"\xff\xd9")


async def test_viewer_gets_the_latest_frame_not_a_backlog(base: str) -> None:
    red, blue = jpeg((255, 0, 0)), jpeg((0, 0, 255))
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(f"ws://{base}/video/main/ingest") as ws,
    ):
        await ws.send_bytes(red)
        await ws.send_bytes(blue)
        await asyncio.sleep(0.1)
        async with session.get(f"http://{base}/video/main/stream.mjpg") as response:
            assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
            first = await asyncio.wait_for(read_part(response), 2)
            assert first.startswith(b"\xff\xd8") and first.endswith(b"\xff\xd9")
            assert dominant_channel(first) == 2  # the newest frame, not the backlog
            await ws.send_bytes(red)
            second = await asyncio.wait_for(read_part(response), 2)
            assert dominant_channel(second) == 0


async def test_non_jpeg_data_is_ignored_and_bad_names_rejected(base: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"ws://{base}/video/main/ingest") as ws:
            await ws.send_bytes(b"not a jpeg")
            await ws.send_str("text")
            await ws.send_bytes(jpeg((0, 255, 0)))
            await asyncio.sleep(0.1)
            async with session.get(f"http://{base}/video/main/stream.mjpg") as response:
                part = await asyncio.wait_for(read_part(response), 2)
                assert part.startswith(b"\xff\xd8")
        async with session.get(f"http://{base}/video/Bad!/stream.mjpg") as response:
            assert response.status == 400


async def test_browser_from_a_foreign_origin_cannot_ingest(base: str) -> None:
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.WSServerHandshakeError):
            await session.ws_connect(
                f"ws://{base}/video/main/ingest", headers={"Origin": "https://evil.example"}
            )


async def test_video_stream_pushes_frames_from_a_thread(base: str) -> None:
    async with HubClient(f"ws://{base}/ws") as client:
        stream = VideoStream(
            "main", ingest_url(client.url, "main"), client.session, asyncio.get_running_loop()
        )
        task = stream.start()
        try:
            await asyncio.to_thread(stream.push_jpeg, jpeg((255, 0, 0)))
            async with client.session.get(f"http://{base}/video/main/stream.mjpg") as response:
                part = await asyncio.wait_for(read_part(response), 3)
                assert part.startswith(b"\xff\xd8")
        finally:
            await stream.stop(task)
    with pytest.raises(ValueError):
        stream.push_jpeg(b"nope")
