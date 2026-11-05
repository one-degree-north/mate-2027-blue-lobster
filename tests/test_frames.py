"""Frames flowing from the hub to modules, and streams opening and closing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import aiohttp
import pytest
from PIL import Image

from rovkit import Module
from rovkit.client import HubClient
from rovkit.protocol import Message
from rovkit.video import JPEG_MAGIC, FrameSubscription, encode_jpeg, frames_url

from .test_module import eventually


def jpeg(colour: tuple[int, int, int]) -> bytes:
    return encode_jpeg(Image.new("RGB", (16, 16), colour))


def http_base(hub_url: str) -> str:
    return hub_url.removeprefix("ws://").removesuffix("/ws")


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


class Streamer(Module):
    async def on_start(self) -> None:
        self.stream = self.video("cam")


async def test_subscriber_gets_the_newest_frame_and_skips_the_backlog(
    hub_url: str, session: aiohttp.ClientSession
) -> None:
    red, green, blue = jpeg((255, 0, 0)), jpeg((0, 255, 0)), jpeg((0, 0, 255))
    base = http_base(hub_url)
    async with session.ws_connect(f"ws://{base}/video/cam/ingest") as source:
        await source.send_bytes(red)
        await asyncio.sleep(0.05)
        async with FrameSubscription("cam", frames_url(hub_url, "cam"), session) as frames:
            assert await frames.next(2) == red  # a late joiner starts with the current frame

            await source.send_bytes(green)
            await source.send_bytes(blue)
            await asyncio.sleep(0.2)
            assert await frames.next(2) == blue  # the backlog (green) is skipped
            with pytest.raises(TimeoutError):
                await frames.next(0.1)  # nothing new

            await source.send_bytes(red)
            assert await frames.next(2) == red


async def test_subscribing_before_the_source_exists_waits_for_it(
    hub_url: str, session: aiohttp.ClientSession
) -> None:
    async with FrameSubscription("cam", frames_url(hub_url, "cam"), session) as frames:
        with pytest.raises(TimeoutError):
            await frames.next(0.1)
        async with session.ws_connect(f"ws://{http_base(hub_url)}/video/cam/ingest") as source:
            await source.send_bytes(jpeg((255, 0, 0)))
            assert (await frames.next(2)).startswith(JPEG_MAGIC)


async def test_next_outside_the_with_block_is_an_error(
    hub_url: str, session: aiohttp.ClientSession
) -> None:
    with pytest.raises(RuntimeError, match="async with"):
        await FrameSubscription("cam", frames_url(hub_url, "cam"), session).next(0.1)


async def test_a_closed_link_frees_the_hub_handler(
    hub_url: str, session: aiohttp.ClientSession
) -> None:
    before = len(asyncio.all_tasks())
    async with session.ws_connect(frames_url(hub_url, "cam")):
        await asyncio.sleep(0.1)
        assert len(asyncio.all_tasks()) > before  # the handler and its two helper tasks
    await asyncio.sleep(0.3)
    assert len(asyncio.all_tasks()) == before


async def test_browser_from_a_foreign_origin_cannot_read_frames(
    hub_url: str, session: aiohttp.ClientSession
) -> None:
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await session.ws_connect(
            frames_url(hub_url, "cam"), headers={"Origin": "https://evil.example"}
        )


async def test_module_frames_reads_a_named_or_the_default_stream(
    hub_url: str, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    red = jpeg((255, 0, 0))
    monkeypatch.setattr("rovkit.module.DEFAULT_SOURCE", "cam")
    async with HubClient(hub_url) as client:
        module = Streamer("streamer", client)
        with pytest.raises(ValueError, match="must match"):
            module.frames("Bad Name!")
        async with session.ws_connect(f"ws://{http_base(hub_url)}/video/cam/ingest") as source:
            await source.send_bytes(red)
            await asyncio.sleep(0.05)
            for name in ("cam", None):
                async with module.frames(name) as frames:
                    assert await frames.next(2) == red


async def test_closing_a_stream_removes_it_from_the_ui(hub_url: str) -> None:
    seen: dict[str, Message] = {}
    async with HubClient(hub_url) as ui, HubClient(hub_url) as client:
        await ui.subscribe("video/*", lambda m: seen.__setitem__(m.topic, m))
        stop = asyncio.Event()
        module = Streamer("streamer", client)
        task = asyncio.create_task(module._serve(stop))

        def listed() -> bool:
            message = seen.get("video/cam/meta")
            return message is not None and message.data is not None

        await eventually(listed)
        with pytest.raises(ValueError, match="already open"):
            module.video("cam")

        await module.stream.close()
        await eventually(lambda: not listed())
        module.stream = module.video("cam")  # a closed name can be opened again
        await eventually(listed)

        stop.set()  # shutting down closes whatever is still open
        await asyncio.wait_for(task, 5)
        await eventually(lambda: not listed())


async def test_spawned_task_failures_are_logged(
    hub_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom() -> None:
        raise RuntimeError("kaboom")

    async with HubClient(hub_url) as client:
        module = Streamer("streamer", client)
        with caplog.at_level(logging.ERROR):
            task = module.spawn(boom(), "boom")
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)  # done-callbacks run on the next loop iteration
    failures = [r for r in caplog.records if r.exc_info and "boom" in r.getMessage()]
    assert failures
    error = failures[0].exc_info
    assert isinstance(error, tuple)
    assert str(error[1]) == "kaboom"
