from __future__ import annotations

import importlib
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

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


def load_photogrammetry_package() -> ModuleType:
    """Import modules/photogrammetry the way the runner does, and return the package."""
    from rovkit.runner import load_module_class

    cls = load_module_class(Path(__file__).parent.parent / "modules" / "photogrammetry")
    package = sys.modules[cls.__module__]
    importlib.import_module(f"{package.__name__}.capture")
    return package
