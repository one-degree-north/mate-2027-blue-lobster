from __future__ import annotations

import socket
from collections.abc import AsyncIterator

import pytest
from aiohttp import web

from rovkit.hub import create_app
from rovkit.protocol import WS_PATH


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def start_hub(port: int) -> web.AppRunner:
    runner = web.AppRunner(create_app(), handle_signals=False)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


@pytest.fixture
async def hub_url() -> AsyncIterator[str]:
    port = free_port()
    runner = await start_hub(port)
    yield f"ws://127.0.0.1:{port}{WS_PATH}"
    await runner.cleanup()
