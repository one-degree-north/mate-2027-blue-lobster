"""Sources, the JPEG splitter, and GStreamer sources (run here with a fake gst-launch)."""

from __future__ import annotations

import asyncio
import io
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from PIL import Image

from rovkit import Slider, Source
from rovkit.cli import _select_sources
from rovkit.client import HubClient
from rovkit.gstreamer import GstSource, JpegSplitter, no_signal_jpeg
from rovkit.protocol import Message
from rovkit.supervisor import Supervisor, discover
from rovkit.video import JPEG_MAGIC, FrameSubscription, frames_url

TIMEOUT = 6.0


async def eventually(predicate: Callable[[], Any]) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not predicate():  # noqa: ASYNC110 - polling a plain dict
            await asyncio.sleep(0.02)


def info_state(seen: dict[str, Message], source: str = "fake") -> str | None:
    message = seen.get(f"sources/{source}/info")
    return message.data["state"] if message else None


@pytest.fixture
async def seen(hub_url: str) -> AsyncIterator[dict[str, Message]]:
    messages: dict[str, Message] = {}
    async with HubClient(hub_url) as client:
        for pattern in ("modules/*", "sources/*", "video/*"):
            await client.subscribe(pattern, lambda m: messages.__setitem__(m.topic, m))
        yield messages


# -- Source ------------------------------------------------------------------------------


class Cam(Source):
    async def run(self) -> None:
        self.video("cam")
        self.report("live", fps=12.5)


async def test_a_source_reports_but_gets_no_panel(hub_url: str, seen: dict[str, Message]) -> None:
    stop = asyncio.Event()
    async with HubClient(hub_url) as client:
        task = asyncio.create_task(Cam("cam", client)._serve(stop))
        await eventually(lambda: "sources/cam/info" in seen and "video/cam/meta" in seen)
        assert seen["sources/cam/info"].data == {"state": "live", "fps": 12.5}
        assert "modules/cam/schema" not in seen  # no schema, so the UI builds no panel
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)


def test_a_source_cannot_declare_widgets() -> None:
    with pytest.raises(TypeError, match="no panel"):

        class Bad(Source):
            gain = Slider(0, 1)


SOURCE_FILE = """
from rovkit import Source

class Cam(Source):
    async def run(self):
        self.report("live", fps=30.0)
"""


async def test_the_supervisor_runs_sources_and_reports_them_under_sources(
    hub_url: str, seen: dict[str, Message], tmp_path: Path
) -> None:
    (tmp_path / "cam.py").write_text(SOURCE_FILE)
    specs = discover(tmp_path, "source")
    assert [(s.id, s.kind) for s in specs] == [("cam", "source")]
    supervisor = Supervisor(specs, hub_url)
    task = asyncio.create_task(supervisor.run())
    try:
        await eventually(lambda: "sources/cam/info" in seen)
        assert seen["sources/cam/status"].data["state"] == "running"
        assert seen["modules/_index"].data == []  # a source has no panel to keep
        assert "modules/cam/status" not in seen
    finally:
        supervisor.stop()
        await asyncio.wait_for(task, 15)
    await eventually(lambda: seen["sources/cam/status"].data["state"] == "stopped")


def test_select_sources_by_name(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        (tmp_path / f"{name}.py").write_text("")
    assert [s.id for s in _select_sources(tmp_path, [])] == ["alpha", "beta"]
    assert [s.id for s in _select_sources(tmp_path, ["beta"])] == ["beta"]
    with pytest.raises(SystemExit, match=r"unknown source nope \(available: alpha, beta\)"):
        _select_sources(tmp_path, ["nope"])
    assert _select_sources(tmp_path / "missing", []) == []


# -- JpegSplitter ------------------------------------------------------------------------


def make_jpeg(seed: int, **options: Any) -> bytes:
    """A JPEG of random noise: lots of FF00 stuffing in the compressed data."""
    noise = Image.frombytes(
        "RGB", (48, 32), bytes((seed * 37 + i * 7919) % 251 for i in range(48 * 32 * 3))
    )
    buffer = io.BytesIO()
    noise.save(buffer, format="JPEG", quality=85, **options)
    return buffer.getvalue()


def split_in_chunks(data: bytes, size: int) -> list[bytes]:
    splitter = JpegSplitter()
    frames: list[bytes] = []
    for start in range(0, len(data), size):
        frames += splitter.feed(data[start : start + size])
    return frames


FRAMES = [make_jpeg(1), make_jpeg(2, progressive=True), make_jpeg(3, restart_marker_blocks=4)]


@pytest.mark.parametrize("chunk", [1, 7, 500, 1 << 20])
def test_splitter_finds_every_frame_however_the_bytes_arrive(chunk: int) -> None:
    assert split_in_chunks(b"".join(FRAMES), chunk) == FRAMES


def test_splitter_drops_garbage_between_frames() -> None:
    data = b"\x00junk\xff" + FRAMES[0] + b"\xde\xad" + FRAMES[1]
    assert split_in_chunks(data, 64) == [FRAMES[0], FRAMES[1]]


def test_splitter_is_not_fooled_by_an_end_marker_inside_a_header() -> None:
    payload = b"\xff\xd9\xff\xd8 not the end"
    comment = b"\xff\xfe" + (2 + len(payload)).to_bytes(2, "big") + payload
    tricky = FRAMES[0][:2] + comment + FRAMES[0][2:]
    assert split_in_chunks(tricky + FRAMES[1], 100) == [tricky, FRAMES[1]]


def test_splitter_holds_back_an_unfinished_frame() -> None:
    splitter = JpegSplitter()
    assert splitter.feed(FRAMES[0][:-1]) == []
    assert splitter.feed(FRAMES[0][-1:]) == [FRAMES[0]]


def test_no_signal_picture_is_a_jpeg() -> None:
    data = no_signal_jpeg()
    assert data.startswith(JPEG_MAGIC)
    assert Image.open(io.BytesIO(data)).size == (640, 360)


# -- GstSource, with a fake gst-launch ---------------------------------------------------

FAKE_PIPELINE = """
import io, os, sys, time
from PIL import Image

buffer = io.BytesIO()
Image.new("RGB", (16, 16), (255, 0, 0)).save(buffer, "JPEG")
frame = buffer.getvalue()
with open(os.environ["FAKE_RUNS"], "a") as runs:
    runs.write("started\\n")
for _ in range(int(os.environ["FAKE_FRAMES"])):
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    time.sleep(0.05)
time.sleep(float(os.environ["FAKE_LINGER"]))
"""


@pytest.fixture
def fake_gst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    script = tmp_path / "fake_gst.py"
    script.write_text(FAKE_PIPELINE)
    monkeypatch.setenv("FAKE_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("FAKE_FRAMES", "40")
    monkeypatch.setenv("FAKE_LINGER", "30")
    return (sys.executable, str(script))


def runs(tmp_path: Path) -> int:
    path = tmp_path / "runs"
    return len(path.read_text().splitlines()) if path.exists() else 0


async def run_source(
    hub_url: str, launcher: tuple[str, ...], **settings: Any
) -> tuple[asyncio.Event, asyncio.Task[None], HubClient]:
    class Fake(GstSource):
        stream = "fake"

        def pipeline(self) -> str:
            return ""

    Fake.launcher = launcher
    for name, value in settings.items():
        setattr(Fake, name, value)
    stop = asyncio.Event()
    client = HubClient(hub_url)
    await client.start()
    return stop, asyncio.create_task(Fake("fake", client)._serve(stop)), client


async def test_gst_source_publishes_frames_and_reports_live(
    hub_url: str, seen: dict[str, Message], fake_gst: tuple[str, ...]
) -> None:
    stop, task, client = await run_source(hub_url, fake_gst)
    try:
        async with (
            aiohttp.ClientSession() as session,
            FrameSubscription("fake", frames_url(hub_url, "fake"), session) as frames,
        ):
            assert (await frames.next(TIMEOUT)).startswith(JPEG_MAGIC)
        await eventually(lambda: info_state(seen) == "live")
        assert seen["sources/fake/info"].data["fps"] > 5
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()


async def test_gst_source_shows_no_signal_when_the_pipeline_stalls(
    hub_url: str,
    seen: dict[str, Message],
    fake_gst: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FRAMES", "0")  # starts, then says nothing
    stop, task, client = await run_source(hub_url, fake_gst, no_signal_after=0.3)
    try:
        async with (
            aiohttp.ClientSession() as session,
            FrameSubscription("fake", frames_url(hub_url, "fake"), session) as frames,
        ):
            picture = await frames.next(TIMEOUT)
        assert Image.open(io.BytesIO(picture)).size == (640, 360)  # the NO SIGNAL picture
        await eventually(lambda: info_state(seen) == "no signal")
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()


async def test_gst_source_restarts_a_pipeline_that_exits(
    hub_url: str, fake_gst: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FRAMES", "2")
    monkeypatch.setenv("FAKE_LINGER", "0")
    stop, task, client = await run_source(hub_url, fake_gst, restart_delay=0.1)
    try:
        await eventually(lambda: runs(tmp_path) >= 2)
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()


async def test_gst_source_says_so_when_gstreamer_is_missing(
    hub_url: str, seen: dict[str, Message]
) -> None:
    stop, task, client = await run_source(hub_url, ("no-such-gst-launch-1.0",))
    try:
        await eventually(lambda: info_state(seen) == "error")
        assert "install GStreamer" in seen["sources/fake/info"].data["detail"]
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()


def test_the_real_sources_build_their_pipelines() -> None:
    """No GStreamer needed: just check the descriptions are well formed."""
    import importlib.util

    root = Path(__file__).parent.parent / "sources"
    for name, must_contain in (("gst_camera", "udpsrc port=5600"), ("video_file", "filesrc")):
        spec = importlib.util.spec_from_file_location(f"sources_{name}", root / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = next(
            cls
            for cls in vars(module).values()
            if isinstance(cls, type) and issubclass(cls, GstSource) and cls is not GstSource
        )
        description = source.pipeline(object.__new__(source))  # only reads class settings
        assert must_contain in description
        assert (
            description.rstrip().endswith("fdsink fd=1") or "fdsink fd=1 sync=false" in description
        )
        assert "jpegenc" in description


async def test_a_source_that_cannot_start_complains_once_not_every_check(
    hub_url: str,
    seen: dict[str, Message],
    fake_gst: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="module.fake")
    stop, task, client = await run_source(
        hub_url, fake_gst, retry_wait=0.03, problem=lambda self: "video file not found: x.mp4"
    )
    try:
        await eventually(lambda: info_state(seen) == "error")
        await asyncio.sleep(0.5)  # about sixteen more checks
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert [r.getMessage() for r in errors] == ["video file not found: x.mp4"]
    assert seen["sources/fake/info"].data["detail"] == "video file not found: x.mp4"


async def test_a_source_with_nothing_to_do_is_idle_not_an_error(
    hub_url: str,
    seen: dict[str, Message],
    fake_gst: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="module.fake")
    stop, task, client = await run_source(
        hub_url, fake_gst, retry_wait=0.03, idle_reason=lambda self: "no clip chosen"
    )
    try:
        await eventually(lambda: info_state(seen) == "idle")
        await asyncio.sleep(0.2)
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()
    assert not [r for r in caplog.records if r.levelno >= 40]  # nothing at ERROR or above
    assert seen["sources/fake/info"].data == {"state": "idle", "detail": "no clip chosen"}


async def test_a_waiting_source_starts_once_the_problem_is_fixed(
    hub_url: str, seen: dict[str, Message], fake_gst: tuple[str, ...]
) -> None:
    fixed = False
    stop, task, client = await run_source(
        hub_url,
        fake_gst,
        retry_wait=0.03,
        problem=lambda self: None if fixed else "video file not found",
    )
    try:
        await eventually(lambda: info_state(seen) == "error")
        fixed = True
        await eventually(lambda: info_state(seen) == "live")
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()


async def test_a_pipeline_that_keeps_dying_is_restarted_more_and_more_slowly(
    hub_url: str, fake_gst: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_LINGER", "0")  # exits straight away, every time
    stop, task, client = await run_source(hub_url, fake_gst, restart_delay=0.05)
    try:
        await asyncio.sleep(1.5)
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        await client.close()
    assert 2 <= runs(tmp_path) <= 5  # a fixed 0.05 s pause would have restarted it ~8 times
