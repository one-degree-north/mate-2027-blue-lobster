"""Photo capture for photogrammetry: time-based, sharpest frame wins, ordered file names."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageFilter

from .conftest import load_photogrammetry_package

capture_module = load_photogrammetry_package().capture
Capture = capture_module.Capture
sharpness = capture_module.sharpness


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def noise_jpeg(blur: float = 0.0) -> bytes:
    image = Image.frombytes("RGB", (96, 64), os.urandom(96 * 64 * 3))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def test_a_sharp_picture_scores_higher_than_a_blurred_one() -> None:
    assert sharpness(noise_jpeg()) > sharpness(noise_jpeg(blur=3)) * 5
    assert sharpness(b"not a jpeg") == -1.0


def make(tmp_path: Path, hz: float = 2.0) -> tuple[Capture, Clock]:
    clock = Clock()
    capture = Capture(tmp_path / "frames", hz, clock=clock, measure=lambda data: float(len(data)))
    capture.start()
    return capture, clock


def names(capture: Capture) -> list[str]:
    return sorted(p.name for p in capture.folder.iterdir())


def test_one_photo_per_interval_whatever_the_frame_rate(tmp_path: Path) -> None:
    capture, clock = make(tmp_path, hz=2.0)  # a photo every 0.5 s
    for fps in (60, 5):  # a fast source, then a slow one: two seconds each
        for _ in range(fps * 2):
            capture.offer(b"x" * 10)
            clock.now += 1 / fps
    assert 7 <= capture.count <= 9  # ~4 seconds at 2 per second, not 600 frames' worth


def test_the_sharpest_frame_of_each_interval_is_kept(tmp_path: Path) -> None:
    capture, clock = make(tmp_path, hz=1.0)
    frames = [b"a" * 5, b"b" * 50, b"c" * 20]  # "sharpness" is the length here
    for frame in frames:
        capture.offer(frame)
        clock.now += 0.3
    clock.now += 0.2
    capture.offer(b"d" * 9)  # past one second: the interval closes
    assert (capture.folder / "frame_00001.jpg").read_bytes() == b"b" * 50


def test_finish_saves_the_unfinished_interval_and_then_ignores_frames(tmp_path: Path) -> None:
    capture, _ = make(tmp_path)
    capture.offer(b"only frame")
    assert capture.count == 0  # the interval is still open
    assert capture.finish() == 1
    assert capture.offer(b"too late") == 1
    assert capture.finish() == 1  # calling it twice saves nothing more
    assert names(capture) == ["frame_00001.jpg"]


def test_finish_with_no_frames_saves_nothing(tmp_path: Path) -> None:
    capture, _ = make(tmp_path)
    assert capture.finish() == 0
    assert names(capture) == []


def test_names_are_zero_padded_so_text_order_is_capture_order(tmp_path: Path) -> None:
    capture, clock = make(tmp_path, hz=10.0)
    for _ in range(12):
        capture.offer(b"x")
        clock.now += 0.11
    capture.finish()
    saved = names(capture)
    assert saved == sorted(saved) and saved[0] == "frame_00001.jpg"
    assert saved.index("frame_00002.jpg") < saved.index("frame_00010.jpg")  # not "…_10" first


def test_start_empties_a_folder_left_by_an_earlier_capture(tmp_path: Path) -> None:
    capture, _ = make(tmp_path)
    capture.offer(b"x")
    capture.finish()
    again = Capture(capture.folder, 2.0)
    again.start()
    assert names(again) == []


def test_the_rate_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Capture(tmp_path, 0)
