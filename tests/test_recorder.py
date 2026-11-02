from __future__ import annotations

import asyncio
from pathlib import Path

from mcap.reader import make_reader

from rovkit.client import HubClient
from rovkit.protocol import Message
from rovkit.recorder import record, replay

TIMEOUT = 3.0


async def test_record_then_replay_preserves_order_and_state(hub_url: str, tmp_path: Path) -> None:
    path = tmp_path / "run.mcap"
    stop = asyncio.Event()
    task = asyncio.create_task(record(hub_url, path, stop))
    async with HubClient(hub_url) as client:
        await asyncio.sleep(0.2)  # let the recorder subscribe
        await client.publish("modules/a/state/depth", 1.5, retain=True)
        await client.publish("modules/a/cmd/go", None)
        await client.publish("modules/a/state/depth", 2.5, retain=True)
        await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, TIMEOUT)

    with path.open("rb") as f:
        recorded = [(c.topic, m.data) for _, c, m in make_reader(f).iter_messages()]
    assert recorded == [
        ("modules/a/state/depth", b"1.5"),
        ("modules/a/cmd/go", b"null"),
        ("modules/a/state/depth", b"2.5"),
    ]

    await replay(hub_url, path, speed=100)
    seen: asyncio.Queue[Message] = asyncio.Queue()
    async with HubClient(hub_url) as late:  # joins after replay: only retained state arrives
        await late.subscribe("modules/*", seen.put_nowait)
        first = await asyncio.wait_for(seen.get(), TIMEOUT)
        assert (first.topic, first.data) == ("modules/a/state/depth", 2.5)
        await asyncio.sleep(0.1)
        assert seen.empty()  # the one-shot command was not retained
