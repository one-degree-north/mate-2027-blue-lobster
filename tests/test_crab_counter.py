"""The crab counter module with its real tracker, driven the way the UI drives it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
import cv2
import numpy as np
import pytest

from rovkit.client import HubClient
from rovkit.protocol import Message, cmd_topic
from rovkit.runner import load_module_class
from rovkit.video import JPEG_MAGIC, FrameSubscription, frames_url

MODULE = Path(__file__).parent.parent / "modules" / "crab_counter"
TIMEOUT = 20.0


async def eventually(predicate: Callable[[], Any]) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not predicate():  # noqa: ASYNC110 - polling a plain dict
            await asyncio.sleep(0.05)


def frame_with_a_crab() -> bytes:
    """A dark 1280x720 frame with the reference crab image pasted in, as a JPEG."""
    crab = cv2.imread(str(MODULE / "assets" / "monkeydo.png"))
    assert crab is not None
    canvas = np.full((720, 1280, 3), 30, np.uint8)
    canvas[200 : 200 + crab.shape[0], 400 : 400 + crab.shape[1]] = crab
    return cv2.imencode(".jpg", canvas)[1].tobytes()


async def test_start_counts_the_crab_and_stop_closes_everything(
    hub_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rovkit.module.DEFAULT_SOURCE", "cam")
    seen: dict[str, Message] = {}
    crab = frame_with_a_crab()
    stop = asyncio.Event()
    async with (
        aiohttp.ClientSession() as session,
        HubClient(hub_url) as ui,
        HubClient(hub_url) as client,
    ):
        for pattern in ("modules/*", "video/*"):
            await ui.subscribe(pattern, lambda m: seen.__setitem__(m.topic, m))

        def value(widget: str) -> Any:
            message = seen.get(f"modules/crab_counter/state/{widget}")
            return message.data if message else None

        def status() -> Any:
            message = seen.get("modules/crab_counter/state/status")
            return message.data["text"] if message else None

        def listed() -> bool:
            message = seen.get("video/crab_counter/meta")
            return message is not None and message.data is not None

        module = load_module_class(MODULE)("crab_counter", client)
        task = asyncio.create_task(module._serve(stop))
        await eventually(lambda: status() == "Stopped")
        assert not listed()  # nothing is published until Start is pressed

        host = hub_url.removeprefix("ws://").removesuffix("/ws")
        async with session.ws_connect(f"ws://{host}/video/cam/ingest") as camera:

            async def feed() -> None:
                while True:
                    await camera.send_bytes(crab)
                    await asyncio.sleep(0.1)

            feeder = asyncio.create_task(feed())
            try:
                await ui.publish(cmd_topic("crab_counter", "detection_interval"), 1)
                await ui.publish(cmd_topic("crab_counter", "start_counter"))
                await eventually(lambda: value("total") == 1)  # counted once, not once per frame
                await eventually(listed)
                assert status() == "Counting…"
                assert value("visible") == 1

                # The annotated stream is a real JPEG other people can watch.
                url = frames_url(hub_url, "crab_counter")
                async with FrameSubscription("crab_counter", url, session) as frames:
                    assert (await frames.next(TIMEOUT)).startswith(JPEG_MAGIC)

                await ui.publish(cmd_topic("crab_counter", "stop_counter"))
                await eventually(lambda: status() == "Stopped")
                await eventually(lambda: not listed())  # the stream leaves the dropdown
                assert value("total") == 1  # stopping keeps the count

                await ui.publish(cmd_topic("crab_counter", "reset_counter"))
                await eventually(lambda: value("total") == 0)
            finally:
                feeder.cancel()
        stop.set()
        await asyncio.wait_for(task, 10)
