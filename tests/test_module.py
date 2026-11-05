from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from rovkit import Button, Module, Readout, Slider, Status, Toggle
from rovkit.client import HubClient
from rovkit.protocol import Message, cmd_topic, disabled_topic, schema_topic, state_topic

TIMEOUT = 2.0


class Sample(Module):
    title = "Sample"
    gain = Slider(0, 10, step=1, default=2)
    armed = Toggle()
    ping = Button("Ping")
    seen = Readout("Seen", default=0)

    async def on_start(self) -> None:
        self.events: list[Any] = []

    async def on_gain(self, value: float) -> None:
        self.events.append(("gain", value))

    async def on_armed(self, value: bool) -> None:
        self.events.append(("armed", value))
        raise RuntimeError("handler bugs must not kill the module")

    async def on_ping(self) -> None:
        self.events.append("ping")
        self.seen = len(self.events)


class Crasher(Module):
    async def run(self) -> None:
        raise RuntimeError("boom")


@pytest.fixture
async def observer(hub_url: str) -> AsyncIterator[tuple[HubClient, dict[str, Message]]]:
    seen: dict[str, Message] = {}
    async with HubClient(hub_url) as client:
        await client.subscribe("modules/*", lambda m: seen.__setitem__(m.topic, m))
        yield client, seen


async def eventually(predicate: Any) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not predicate():  # noqa: ASYNC110 - polling a plain dict
            await asyncio.sleep(0.01)


async def test_module_publishes_schema_and_initial_state(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    _, seen = observer
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        task = asyncio.create_task(Sample("sample", client)._serve(stop))
        await eventually(lambda: state_topic("sample", "armed") in seen)

        schema = seen[schema_topic("sample")].data
        assert schema["title"] == "Sample"
        assert [w["id"] for w in schema["widgets"]] == ["gain", "armed", "ping", "seen"]
        assert seen[state_topic("sample", "gain")].data == 2.0
        assert state_topic("sample", "ping") not in seen  # buttons have no state

        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


async def test_commands_update_state_and_call_handlers_in_order(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    ui, seen = observer
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        module = Sample("sample", client)
        task = asyncio.create_task(module._serve(stop))
        await eventually(lambda: state_topic("sample", "seen") in seen)

        await ui.publish(cmd_topic("sample", "gain"), 3.4)  # snapped to the step
        await ui.publish(cmd_topic("sample", "gain"), 99)  # clamped
        await ui.publish(cmd_topic("sample", "gain"), "loud")  # rejected
        await ui.publish(cmd_topic("sample", "armed"), True)  # handler raises; module survives
        await ui.publish(cmd_topic("sample", "ping"))
        await eventually(lambda: seen[state_topic("sample", "seen")].data == 4)

        assert module.events == [("gain", 3.0), ("gain", 10.0), ("armed", True), "ping"]
        assert module.gain == 10.0
        assert seen[state_topic("sample", "gain")].data == 10.0

        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


async def test_commands_for_unknown_widgets_are_ignored(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    ui, seen = observer
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        task = asyncio.create_task(Sample("sample", client)._serve(stop))
        await eventually(lambda: state_topic("sample", "seen") in seen)
        await ui.publish(cmd_topic("sample", "seen"), 99)  # output-only
        await ui.publish(cmd_topic("sample", "nope"), 1)
        await asyncio.sleep(0.1)
        assert seen[state_topic("sample", "seen")].data == 0
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


async def test_run_crash_propagates(hub_url: str) -> None:
    async with HubClient(hub_url) as client:
        with pytest.raises(RuntimeError, match="boom"):
            await asyncio.wait_for(Crasher("crasher", client)._serve(asyncio.Event()), TIMEOUT)


async def test_assignment_from_another_thread_is_published(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    _, seen = observer
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        module = Sample("sample", client)
        task = asyncio.create_task(module._serve(stop))
        await eventually(lambda: state_topic("sample", "seen") in seen)

        def work() -> None:
            module.seen = "from a thread"

        await asyncio.to_thread(work)
        await eventually(lambda: seen[state_topic("sample", "seen")].data == "from a thread")
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


async def test_disabled_widgets_are_announced_and_refuse_commands(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    ui, seen = observer
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        module = Sample("sample", client)
        task = asyncio.create_task(module._serve(stop))
        await eventually(lambda: disabled_topic("sample") in seen)
        assert seen[disabled_topic("sample")].data == []  # nothing disabled at the start

        module.disable("gain", "ping")
        await eventually(lambda: seen[disabled_topic("sample")].data == ["gain", "ping"])
        await ui.publish(cmd_topic("sample", "gain"), 7)
        await ui.publish(cmd_topic("sample", "ping"))
        await ui.publish(cmd_topic("sample", "armed"), False)  # still enabled: goes through
        await eventually(lambda: ("armed", False) in module.events)
        assert module.gain == 2.0 and "ping" not in module.events  # the disabled two were ignored

        module.enable("gain")
        await eventually(lambda: seen[disabled_topic("sample")].data == ["ping"])
        await ui.publish(cmd_topic("sample", "gain"), 7)
        await eventually(lambda: module.gain == 7.0)

        with pytest.raises(ValueError, match="not an input widget"):
            module.disable("seen")  # output-only
        with pytest.raises(ValueError, match="not an input widget"):
            module.enable("nope")

        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


async def test_a_restarted_module_clears_what_the_last_run_left_disabled(
    hub_url: str, observer: tuple[HubClient, dict[str, Message]]
) -> None:
    _, seen = observer
    async with HubClient(hub_url) as client:
        first = Sample("sample", client)
        stop = asyncio.Event()
        task = asyncio.create_task(first._serve(stop))
        await eventually(lambda: disabled_topic("sample") in seen)
        first.disable("gain")
        await eventually(lambda: seen[disabled_topic("sample")].data == ["gain"])
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

        stop = asyncio.Event()
        task = asyncio.create_task(Sample("sample", client)._serve(stop))
        await eventually(lambda: seen[disabled_topic("sample")].data == [])
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


class Reporter(Module):
    status = Status("Status")
    count = Readout("Count", default=0)


async def test_update_publishes_only_changes_and_status_carries_its_tone(
    hub_url: str,
) -> None:
    published: list[tuple[str, Any]] = []
    stop = asyncio.Event()
    async with HubClient(hub_url) as ui, HubClient(hub_url) as client:
        await ui.subscribe(
            "modules/reporter/state/*", lambda m: published.append((m.topic, m.data))
        )
        module = Reporter("reporter", client)
        task = asyncio.create_task(module._serve(stop))
        await eventually(lambda: len(published) == 2)  # the two initial values

        module.update("status", ("Done", "ok"))
        module.update("status", ("Done", "ok"))  # unchanged: nothing is published
        module.update("count", 1)
        module.update("count", 1)
        module.status = "Plain text"  # plain assignment still works, with the default tone
        await eventually(lambda: len(published) == 5)
        await asyncio.sleep(0.2)

        assert published[2:] == [
            ("modules/reporter/state/status", {"text": "Done", "tone": "ok"}),
            ("modules/reporter/state/count", 1),
            ("modules/reporter/state/status", {"text": "Plain text", "tone": "info"}),
        ]
        with pytest.raises(ValueError, match="no widget"):
            module.update("nope", 1)
        with pytest.raises(ValueError, match="tone"):
            module.update("status", ("x", "loud"))
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
