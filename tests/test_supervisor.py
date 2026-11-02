from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from rovkit import supervisor
from rovkit.client import HubClient
from rovkit.protocol import Message, schema_topic, status_topic
from rovkit.supervisor import Supervisor, discover

OK_MODULE = """
from rovkit import Module, Readout

class Ok(Module):
    value = Readout("Value", default=VALUE)
"""

CRASH_MODULE = """
from rovkit import Module

class Crash(Module):
    async def run(self):
        raise RuntimeError("boom")
"""


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "MIN_BACKOFF", 0.2)
    monkeypatch.setattr(supervisor, "RELOAD_POLL", 0.1)


def write_ok(path: Path, value: int = 1) -> None:
    path.write_text(OK_MODULE.replace("VALUE", str(value)))


def test_discover_finds_files_and_packages_and_skips_the_rest(tmp_path: Path) -> None:
    write_ok(tmp_path / "alpha.py")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "_private.py").write_text("")
    (tmp_path / "BadName.py").write_text("")
    (tmp_path / "notpkg").mkdir()
    (tmp_path / "notes.txt").write_text("")
    assert [s.id for s in discover(tmp_path)] == ["alpha", "pkg"]


@pytest.fixture
async def statuses(hub_url: str) -> AsyncIterator[dict[str, Message]]:
    seen: dict[str, Message] = {}
    async with HubClient(hub_url) as client:
        await client.subscribe("modules/*", lambda m: seen.__setitem__(m.topic, m))
        yield seen


async def eventually(predicate: Any) -> None:
    async with asyncio.timeout(10):
        while not predicate():  # noqa: ASYNC110 - polling a plain dict
            await asyncio.sleep(0.05)


def restarts(seen: dict[str, Message], module_id: str) -> int:
    message = seen.get(status_topic(module_id))
    return int(message.data["restarts"]) if message else 0


crashed_tails: list[list[str]] = []


def crash_tail(seen: dict[str, Message]) -> bool:
    message = seen.get(status_topic("crash"))
    if message and message.data["state"] == "crashed":
        crashed_tails.append(message.data["tail"])
    return any("boom" in line for tail in crashed_tails for line in tail)


def state_of(seen: dict[str, Message], module_id: str) -> str | None:
    message = seen.get(status_topic(module_id))
    return message.data["state"] if message else None


async def test_supervisor_runs_restarts_and_stops_modules(
    hub_url: str, statuses: dict[str, Message], tmp_path: Path
) -> None:
    write_ok(tmp_path / "ok.py")
    (tmp_path / "crash.py").write_text(CRASH_MODULE)
    sup = Supervisor(discover(tmp_path), hub_url)
    task = asyncio.create_task(sup.run())
    try:
        await eventually(lambda: schema_topic("ok") in statuses)
        assert state_of(statuses, "ok") == "running"
        assert statuses[schema_topic("ok")].data["widgets"][0]["id"] == "value"

        # The crashed report carries the module's last output so the UI can show why.
        await eventually(lambda: crash_tail(statuses))
        await eventually(lambda: restarts(statuses, "crash") >= 1)
    finally:
        sup.stop()
        await asyncio.wait_for(task, 15)
    await eventually(lambda: state_of(statuses, "ok") == "stopped")
    await eventually(lambda: state_of(statuses, "crash") == "stopped")


async def test_reload_restarts_a_changed_module(
    hub_url: str, statuses: dict[str, Message], tmp_path: Path
) -> None:
    path = tmp_path / "ok.py"
    write_ok(path, value=1)
    sup = Supervisor(discover(tmp_path), hub_url, reload=True)
    task = asyncio.create_task(sup.run())
    try:
        await eventually(lambda: schema_topic("ok") in statuses)
        await asyncio.sleep(0.3)
        write_ok(path, value=2)
        path.touch()
        await eventually(lambda: restarts(statuses, "ok") >= 1)
        await eventually(
            lambda: (
                statuses.get("modules/ok/state/value") is not None
                and statuses["modules/ok/state/value"].data == 2
            )
        )
    finally:
        sup.stop()
        await asyncio.wait_for(task, 15)
