"""The photogrammetry module, driven like the UI drives it, with a fake RealityKit engine."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import aiohttp
import pytest

from rovkit.client import HubClient
from rovkit.protocol import Message, cmd_topic, disabled_topic
from rovkit.runner import load_module_class

from .conftest import load_photogrammetry_package
from .test_video import jpeg

MODULE_DIR = Path(__file__).parent.parent / "modules" / "photogrammetry"
TIMEOUT = 15.0
pgm = load_photogrammetry_package()
State = pgm.State


@dataclass
class Box:
    """Same fields as pgm.BoundingBox, which the module reads."""

    min_x: float = -1.0
    min_y: float = 0.0
    min_z: float = -0.25
    max_x: float = 1.0
    max_y: float = 1.0
    max_z: float = 0.25

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def depth(self) -> float:
        return self.max_z - self.min_z


class FakeEngine:
    """Stands in for pgm.Photogrammetry; the test decides how a reconstruction ends."""

    instances: ClassVar[list[FakeEngine]] = []

    def __init__(self, *, temp_dir: str, output_path: str, **_: Any) -> None:
        self.frames_dir = os.path.join(temp_dir, "reconstruction")
        self.output_path = output_path
        self.detail = 0
        self.state = State.IDLE
        self.progress = 0.0
        self.error: str | None = None
        self.photos: list[str] = []
        self.box = Box()
        FakeEngine.instances.append(self)

    def start_reconstruction(self) -> None:
        self.photos = sorted(os.listdir(self.frames_dir))
        self.state, self.progress, self.error = State.RUNNING, 0.0, None

    def write_model(self) -> None:
        Path(self.output_path).write_bytes(b"usdz")

    def finish(self, state: Any, error: str | None = None, *, model: bool = False) -> None:
        if model:
            self.write_model()
        self.state, self.error = state, error

    def stop_reconstruction(self) -> None:
        self.state = State.CANCELLED

    def is_completed(self) -> bool:
        return self.state >= State.DONE

    def get_progress(self) -> float:
        return self.progress

    def get_eta(self) -> float:
        return 5.0

    def get_state(self) -> Any:
        return self.state

    def get_error(self) -> str | None:
        return self.error

    def get_bounding_box(self, scale: float) -> Box:
        b = self.box
        return Box(*(v * scale for v in (b.min_x, b.min_y, b.min_z, b.max_x, b.max_y, b.max_z)))


class Ui:
    """What a browser would see, and a way to press its controls."""

    def __init__(self, client: HubClient) -> None:
        self.client = client
        self.seen: dict[str, Message] = {}
        self.feeder: asyncio.Task[None] | None = None  # the camera; cancel it to cut the video

    def value(self, widget: str) -> Any:
        message = self.seen.get(f"modules/photogrammetry/state/{widget}")
        return message.data if message else None

    @property
    def status(self) -> tuple[str, str] | None:
        data = self.value("status")
        return (data["text"], data["tone"]) if data else None

    @property
    def disabled(self) -> list[str] | None:
        message = self.seen.get(disabled_topic("photogrammetry"))
        return message.data if message else None

    async def press(self, widget: str, value: Any = None) -> None:
        await self.client.publish(cmd_topic("photogrammetry", widget), value)

    async def eventually(self, predicate: Callable[[], Any]) -> None:
        async with asyncio.timeout(TIMEOUT):
            while not predicate():  # noqa: ASYNC110 - polling a plain dict
                await asyncio.sleep(0.02)


IDLE_LOCKS = ["begin_reconstruction", "cancel"]
CAPTURING_LOCKS = ["capture_hz", "detail", "start_photogrammetry"]


@pytest.fixture
async def rig(hub_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Ui]:
    """The module running against a hub, a fake engine, and a camera feeding stream "cam"."""
    FakeEngine.instances.clear()
    monkeypatch.chdir(tmp_path)  # the module writes pgm-temp/ and output.usdz here
    monkeypatch.setattr("rovkit.module.DEFAULT_SOURCE", "cam")
    cls = load_module_class(MODULE_DIR)
    module = sys.modules[cls.__module__]
    monkeypatch.setattr(module, "unsupported_reason", lambda: None)
    monkeypatch.setattr(module, "Photogrammetry", FakeEngine)
    monkeypatch.setattr(module, "POLL_SECONDS", 0.02)

    host = hub_url.removeprefix("ws://").removesuffix("/ws")
    stop = asyncio.Event()
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(f"ws://{host}/video/cam/ingest") as camera,
        HubClient(hub_url) as ui_client,
        HubClient(hub_url) as module_client,
    ):
        ui = Ui(ui_client)
        await ui_client.subscribe(
            "modules/photogrammetry/*", lambda m: ui.seen.update({m.topic: m})
        )

        async def feed() -> None:
            n = 0
            while True:
                n += 1
                await camera.send_bytes(jpeg((n * 7 % 256, 100, 50)))
                await asyncio.sleep(0.02)

        ui.feeder = feeder = asyncio.create_task(feed())
        task = asyncio.create_task(cls("photogrammetry", module_client)._serve(stop))
        await ui.eventually(lambda: ui.status == ("Idle", "info") and ui.disabled is not None)
        yield ui
        feeder.cancel()
        stop.set()
        await asyncio.wait_for(task, 10)


def engine() -> FakeEngine:
    return FakeEngine.instances[-1]


async def capture_a_few_photos(ui: Ui, count: int = 3) -> None:
    await ui.press("capture_hz", 10)  # a photo every 0.1 s
    await ui.eventually(lambda: ui.value("capture_hz") == 10.0)
    await ui.press("start_photogrammetry")
    await ui.eventually(lambda: ui.status == ("Capturing…", "info"))
    await ui.eventually(lambda: (ui.value("photos") or 0) >= count)


async def test_capture_then_reconstruct_then_done(rig: Ui) -> None:
    ui = rig
    assert ui.disabled == IDLE_LOCKS
    await ui.press("detail", "medium")
    await ui.eventually(lambda: ui.value("detail") == "medium")
    await capture_a_few_photos(ui)

    assert ui.disabled == CAPTURING_LOCKS  # settings are locked while capturing
    await ui.press("capture_hz", 1)
    await ui.press("detail", "raw")
    await asyncio.sleep(0.2)
    assert ui.value("capture_hz") == 10.0 and ui.value("detail") == "medium"  # refused

    await ui.press("begin_reconstruction")
    await ui.eventually(lambda: ui.status == ("Reconstructing…", "info"))
    assert "begin_reconstruction" in (ui.disabled or []) and "cancel" not in (ui.disabled or [])
    await ui.eventually(lambda: engine().state == State.RUNNING)
    assert engine().detail == 2  # "medium" reached the engine as RealityKit's level 2
    assert len(engine().photos) >= 3
    assert engine().photos == sorted(engine().photos)
    assert all(name.startswith("frame_0") and name.endswith(".jpg") for name in engine().photos)

    engine().progress = 0.5
    await ui.eventually(lambda: ui.value("progress") == 0.5)
    await ui.eventually(lambda: ui.value("eta") == 5)
    engine().finish(State.DONE, model=True)
    await ui.eventually(lambda: ui.status == ("Done", "ok"))
    assert ui.value("progress") == 1.0 and ui.value("eta") is None
    assert ui.disabled == IDLE_LOCKS  # ready for the next scan


async def test_a_failed_reconstruction_shows_the_engines_reason(rig: Ui) -> None:
    ui = rig
    await capture_a_few_photos(ui)
    await ui.press("begin_reconstruction")
    await ui.eventually(lambda: ui.status == ("Reconstructing…", "info"))
    engine().finish(State.FAILED, "not enough overlap between photos")
    await ui.eventually(lambda: ui.status is not None and ui.status[1] == "error")
    assert ui.status == ("Reconstruction failed: not enough overlap between photos", "error")
    assert ui.disabled == IDLE_LOCKS  # and the module can be used again


async def test_cancelling_a_reconstruction_asks_the_engine_to_stop(rig: Ui) -> None:
    ui = rig
    await capture_a_few_photos(ui)
    await ui.press("begin_reconstruction")
    await ui.eventually(lambda: ui.status == ("Reconstructing…", "info"))
    await ui.press("cancel")
    await ui.eventually(lambda: ui.status == ("Cancelled", "warn"))
    assert engine().state == State.CANCELLED
    assert ui.value("progress") == 0.0 and ui.disabled == IDLE_LOCKS


async def test_cancelling_a_capture_throws_it_away(rig: Ui) -> None:
    ui = rig
    await capture_a_few_photos(ui, count=1)
    await ui.press("cancel")
    await ui.eventually(lambda: ui.status == ("Capture cancelled", "warn"))
    assert ui.disabled == IDLE_LOCKS
    assert engine().state == State.IDLE  # no reconstruction was started


async def test_beginning_with_no_photos_says_so(rig: Ui, monkeypatch: pytest.MonkeyPatch) -> None:
    ui = rig
    monkeypatch.setattr("rovkit.module.DEFAULT_SOURCE", "silent")  # a stream with no source at all
    await ui.press("start_photogrammetry")
    await ui.eventually(lambda: ui.status == ("Capturing…", "info"))
    await ui.press("begin_reconstruction")
    await ui.eventually(lambda: ui.status == ("No photos were captured", "error"))
    assert ui.disabled == IDLE_LOCKS and engine().state == State.IDLE


async def test_begin_and_cancel_do_nothing_when_idle(rig: Ui) -> None:
    ui = rig
    await ui.press("begin_reconstruction")
    await ui.press("cancel")
    await asyncio.sleep(0.2)
    assert ui.status == ("Idle", "info") and engine().state == State.IDLE


async def test_bounding_box_follows_the_scale_factor(rig: Ui) -> None:
    ui = rig
    await ui.press("get_bounding_box")
    await ui.eventually(lambda: ui.status == ("There is no model yet", "warn"))
    engine().write_model()

    await ui.press("get_bounding_box")
    await ui.eventually(lambda: ui.value("size") == "W: 2.0000  H: 1.0000  D: 0.5000")
    await ui.press("scale_factor", 100)  # the numbers on screen follow without asking again
    await ui.eventually(lambda: ui.value("size") == "W: 200.0000  H: 100.0000  D: 50.0000")
    assert ui.value("corners") == "(-100.000, 0.000, -25.000) → (100.000, 100.000, 25.000)"


@pytest.mark.parametrize(
    ("axis", "length", "factor"),
    [("longest", 30, 15.0), ("width", 30, 15.0), ("height", 30, 30.0), ("depth", 5, 10.0)],
)
async def test_calibrating_from_a_reference_object(
    rig: Ui, axis: str, length: float, factor: float
) -> None:
    ui = rig
    engine().write_model()  # a model 2 x 1 x 0.5 in its own units
    await ui.press("reference_length", length)
    await ui.press("reference_axis", axis)
    await ui.eventually(lambda: ui.value("reference_axis") == axis)
    await ui.press("calibrate")
    await ui.eventually(lambda: ui.value("scale_factor") == factor)
    assert ui.status == (f"Calibrated: scale factor {factor:g}", "ok")
    measured = {"longest": 2.0, "width": 2.0, "height": 1.0, "depth": 0.5}[axis]
    assert f"{measured * factor:.4f}" == f"{length:.4f}"  # the reference now measures its length
    assert ui.value("size") is not None  # and the unknown object's size would be reported too


async def test_calibrating_needs_a_model_and_a_length(rig: Ui) -> None:
    ui = rig
    await ui.press("calibrate")
    await ui.eventually(lambda: ui.status == ("There is no model yet", "warn"))
    engine().write_model()
    await ui.press("calibrate")  # reference length is still 0
    await ui.eventually(lambda: ui.status == ("Enter the reference length first", "warn"))
    assert ui.value("scale_factor") == 1.0


async def test_an_unsupported_machine_disables_everything_and_says_why(
    hub_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cls = load_module_class(MODULE_DIR)
    monkeypatch.setattr(
        sys.modules[cls.__module__], "unsupported_reason", lambda: "Photogrammetry needs macOS"
    )
    stop = asyncio.Event()
    async with HubClient(hub_url) as ui_client, HubClient(hub_url) as module_client:
        ui = Ui(ui_client)
        await ui_client.subscribe(
            "modules/photogrammetry/*", lambda m: ui.seen.update({m.topic: m})
        )
        task = asyncio.create_task(cls("photogrammetry", module_client)._serve(stop))
        await ui.eventually(lambda: ui.status == ("Photogrammetry needs macOS", "error"))
        await ui.eventually(lambda: ui.disabled == list(pgm.CONTROLS))
        stop.set()
        await asyncio.wait_for(task, 10)
