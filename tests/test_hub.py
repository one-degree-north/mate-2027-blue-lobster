from __future__ import annotations

import asyncio

import aiohttp
import pytest

from rovkit.client import HubClient
from rovkit.hub import _Client
from rovkit.protocol import Message

from .conftest import free_port, start_hub

TIMEOUT = 2.0


async def next_message(queue: asyncio.Queue[Message]) -> Message:
    return await asyncio.wait_for(queue.get(), TIMEOUT)


async def collect(client: HubClient, pattern: str) -> asyncio.Queue[Message]:
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await client.subscribe(pattern, queue.put_nowait)
    return queue


async def test_glob_subscription_routes_only_matching_topics(hub_url: str) -> None:
    async with HubClient(hub_url) as sub, HubClient(hub_url) as pub:
        inbox = await collect(sub, "demo/*")
        await sub.publish("demo/sync", None)  # hearing our own message proves the subscribe landed
        assert (await next_message(inbox)).topic == "demo/sync"
        await pub.publish("other/topic", 1)
        await pub.publish("demo/counter", 2)

        message = await next_message(inbox)
        assert (message.topic, message.data) == ("demo/counter", 2)
        assert inbox.empty()
        assert message.ts > 0


async def test_retained_value_is_replayed_to_late_subscriber(hub_url: str) -> None:
    async with HubClient(hub_url) as pub:
        await pub.publish("demo/state", {"n": 1}, retain=True)
        await pub.publish("demo/state", {"n": 2}, retain=True)
        async with HubClient(hub_url) as late:
            inbox = await collect(late, "demo/*")
            message = await next_message(inbox)
            assert message.data == {"n": 2}
            await asyncio.sleep(0.05)
            assert inbox.empty()


async def test_retained_null_clears_topic(hub_url: str) -> None:
    async with HubClient(hub_url) as pub:
        await pub.publish("demo/state", 1, retain=True)
        await pub.publish("demo/state", None, retain=True)
        async with HubClient(hub_url) as late:
            inbox = await collect(late, "demo/*")
            await asyncio.sleep(0.1)
            assert inbox.empty()


async def test_overlapping_patterns_do_not_replay_retained_twice(hub_url: str) -> None:
    async with HubClient(hub_url) as pub:
        await pub.publish("demo/state", 1, retain=True)
        async with HubClient(hub_url) as late:
            first = await collect(late, "demo/*")
            await next_message(first)
            await collect(late, "demo/state")
            await asyncio.sleep(0.1)
            assert first.empty()


async def test_malformed_frame_gets_error_and_connection_survives(hub_url: str) -> None:
    async with aiohttp.ClientSession() as session, session.ws_connect(hub_url) as ws:
        await ws.send_str('{"type": "publish"}')
        reply = await ws.receive_json(timeout=TIMEOUT)
        assert reply["type"] == "error"
        await ws.send_str("not json")
        assert (await ws.receive_json(timeout=TIMEOUT))["type"] == "error"
        await ws.send_json({"type": "subscribe", "patterns": ["a"]})
        await ws.send_json({"type": "publish", "topic": "a", "data": 1})
        assert (await ws.receive_json(timeout=TIMEOUT))["topic"] == "a"


async def test_foreign_browser_origin_is_rejected(hub_url: str) -> None:
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
            await session.ws_connect(hub_url, headers={"Origin": "https://evil.example"})
        assert exc.value.status == 403
        ws = await session.ws_connect(hub_url, headers={"Origin": "http://localhost:5173"})
        await ws.close()


async def test_client_resubscribes_and_replays_retained_after_hub_restart() -> None:
    port = free_port()
    runner = await start_hub(port)
    url = f"ws://127.0.0.1:{port}/ws"
    async with HubClient(url, max_backoff=0.1) as module:
        await module.publish("demo/state", "hello", retain=True)
        inbox = await collect(module, "demo/*")
        await next_message(inbox)  # own retained value comes back

        await runner.cleanup()
        runner = await start_hub(port)
        try:
            # The module re-sends its retained state and re-subscribes, so it hears itself.
            message = await next_message(inbox)
            assert message.data == "hello"
        finally:
            await runner.cleanup()


def test_slow_client_drops_oldest_frames() -> None:
    client = _Client("slow")
    for i in range(client.queue.maxsize + 5):
        client.enqueue(str(i))
    assert client.queue.qsize() == client.queue.maxsize
    assert client.queue.get_nowait() == "5"
