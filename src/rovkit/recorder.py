"""Record hub traffic to an MCAP file and play it back.

MCAP is an open log format (https://mcap.dev) that Foxglove and other tools can open.
Each topic becomes a channel with JSON payloads; ``log_time`` is the hub's timestamp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import IO

from mcap.reader import make_reader
from mcap.writer import Writer

from .client import HubClient
from .protocol import Message

log = logging.getLogger(__name__)

# Small chunks so a power loss costs seconds of data, not megabytes.
CHUNK_SIZE = 128 * 1024

# Topics that describe current state; replay retains them so the UI shows them immediately.
_RETAINED_MARKERS = ("/schema", "/state/", "/status", "/meta")


def new_recording_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory / time.strftime("%Y%m%d-%H%M%S.mcap")


class Recorder:
    def __init__(self, stream: IO[bytes]) -> None:
        self._writer = Writer(stream, chunk_size=CHUNK_SIZE)
        self._writer.start()
        self._channels: dict[str, int] = {}
        self.count = 0

    def write(self, message: Message) -> None:
        channel = self._channels.get(message.topic)
        if channel is None:
            channel = self._writer.register_channel(
                topic=message.topic, message_encoding="json", schema_id=0
            )
            self._channels[message.topic] = channel
        nanos = int(message.ts * 1e9)
        self._writer.add_message(
            channel_id=channel,
            log_time=nanos,
            publish_time=nanos,
            data=json.dumps(message.data).encode(),
        )
        self.count += 1

    def finish(self) -> None:
        self._writer.finish()  # type: ignore[no-untyped-call]


async def record(hub_url: str, path: Path, stop: asyncio.Event) -> None:
    """Record everything on the hub to ``path`` until ``stop`` is set."""
    with path.open("wb") as stream:
        recorder = Recorder(stream)
        try:
            async with HubClient(hub_url) as client:
                await client.subscribe("*", recorder.write)
                log.info("recording to %s", path)
                await stop.wait()
        finally:
            recorder.finish()
            log.info("saved %d messages to %s", recorder.count, path)


async def replay(hub_url: str, path: Path, *, speed: float = 1.0, loop: bool = False) -> None:
    """Publish a recording to the hub with its original timing."""
    async with HubClient(hub_url) as client:
        while True:
            with path.open("rb") as stream:
                first: int | None = None
                started = time.monotonic()
                for _, channel, message in make_reader(stream).iter_messages(log_time_order=True):
                    first = message.log_time if first is None else first
                    due = started + (message.log_time - first) / 1e9 / speed
                    await asyncio.sleep(max(0.0, due - time.monotonic()))
                    topic = channel.topic
                    await client.publish(
                        topic,
                        json.loads(message.data),
                        retain=any(m in topic for m in _RETAINED_MARKERS),
                    )
            if not loop:
                return
