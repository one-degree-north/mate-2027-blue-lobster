"""The COLMAP engine, run against stand-in ``colmap``, ``InterfaceCOLMAP`` and ``TextureMesh``.

The stand-ins are small Python scripts on PATH that print COLMAP-like log lines and write the
files the real tools would. What the real tools print is not checked here: that needs a GPU.
"""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

import pytest
import trimesh

from .conftest import load_photogrammetry_package

pgm = load_photogrammetry_package()
colmap_backend = sys.modules[f"{pgm.__name__}.colmap_backend"]
State = pgm.State

COLMAP = """\
import os, sys, time
from pathlib import Path
args = sys.argv[1:]
assert args[0] == "automatic_reconstructor", args
opts = dict(zip(args[1::2], args[2::2]))
mode = os.environ.get("FAKE_MODE", "ok")
Path(os.environ["FAKE_ARGS"]).write_text(" ".join(args))
print("I20260101 12:00:00.000000 1 feature_extraction.cc:1] Extracting features", flush=True)
print("Processed file [2/4]", flush=True)
if mode == "slow":
    time.sleep(60)
if mode == "fail":
    glog = "E20260101 12:00:01.000000 1 s.cc:9] "
    print(glog + "Dense stereo reconstruction requires CUDA", flush=True)
    sys.exit(1)
print("Registering image #1 (1)", flush=True)
print("Processing view 4 / 4 for a.jpg", flush=True)
if mode != "nomesh":
    dense = Path(opts["--workspace_path"]) / "dense" / "0"
    (dense / "images").mkdir(parents=True)
    (dense / "meshed-poisson.ply").write_text("ply")
"""

INTERFACE = """\
import sys
from pathlib import Path
args = sys.argv[1:]
Path(args[args.index("-o") + 1]).write_text("scene")
"""

TEXTURE = """\
import sys
from pathlib import Path
import trimesh
args = sys.argv[1:]
assert Path(args[args.index("-m") + 1]).name == "meshed-poisson.ply"
assert args[args.index("--export-type") + 1] == "glb"
out = Path(args[args.index("-o") + 1]).with_suffix(".glb")
trimesh.creation.box(extents=(2, 1, 0.5)).export(out)
"""


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand-in tools first on PATH; returns the file the stand-in colmap writes its args to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("colmap", COLMAP),
        ("InterfaceCOLMAP", INTERFACE),
        ("TextureMesh", TEXTURE),
    ):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\n{body}")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(colmap_backend, "OPENMVS_DIR", tmp_path / "no-openmvs")
    args = tmp_path / "colmap-args.txt"
    monkeypatch.setenv("FAKE_ARGS", str(args))
    return args


def make_engine(tmp_path: Path, photos: int = 4, detail: int = 2):
    engine = colmap_backend.ColmapPhotogrammetry(
        detail=detail, temp_dir=str(tmp_path / "pgm-temp"), output_path=str(tmp_path / "out.glb")
    )
    Path(engine.frames_dir).mkdir(parents=True)
    for n in range(photos):
        (Path(engine.frames_dir) / f"frame_{n + 1:05d}.jpg").write_bytes(b"jpeg")
    return engine


def wait_for_end(engine, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not engine.is_completed():
        assert time.monotonic() < deadline, "the reconstruction never finished"
        time.sleep(0.02)


def test_a_reconstruction_writes_a_textured_model_and_reports_done(
    tools: Path, tmp_path: Path
) -> None:
    engine = make_engine(tmp_path, detail=3)
    engine.start_reconstruction()
    wait_for_end(engine)

    assert engine.get_state() == State.DONE and engine.get_error() is None
    assert engine.get_progress() == 1.0
    assert (tmp_path / "out.glb").is_file()
    args = tools.read_text()
    assert "--quality high" in args and "--single_camera 1" in args and "--dense 1" in args
    assert str(tmp_path / "pgm-temp" / "reconstruction") in args  # reads the captured photos
    assert (tmp_path / "pgm-temp" / "colmap.log").read_text().count("$ ") == 3  # one per tool


def test_the_bounding_box_comes_from_the_model_and_scales(tools: Path, tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    wait_for_end(engine)

    box = engine.get_bounding_box(1.0)
    assert (box.width, box.height, box.depth) == pytest.approx((2.0, 1.0, 0.5))
    assert engine.get_bounding_box(10.0).width == pytest.approx(20.0)


def test_a_tool_failing_reports_its_reason_without_log_prefixes(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "fail")
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    wait_for_end(engine)

    assert engine.get_state() == State.FAILED
    assert (
        engine.get_error() == "colmap exited with code 1: Dense stereo reconstruction requires CUDA"
    )
    assert not (tmp_path / "out.glb").exists()


def test_no_mesh_from_colmap_is_a_failure_even_when_it_exits_cleanly(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "nomesh")
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    wait_for_end(engine)

    assert engine.get_state() == State.FAILED
    assert "did not overlap" in (engine.get_error() or "")


def test_too_few_photos_fail_before_any_tool_runs(tools: Path, tmp_path: Path) -> None:
    engine = make_engine(tmp_path, photos=2)
    engine.start_reconstruction()
    wait_for_end(engine)

    assert engine.get_state() == State.FAILED
    assert engine.get_error() == "Only 2 photos: at least 3 are needed"
    assert not tools.exists()  # colmap was never started


def test_missing_tools_are_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(colmap_backend, "OPENMVS_DIR", tmp_path / "no-openmvs")
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    wait_for_end(engine)

    assert engine.get_state() == State.FAILED
    assert (
        engine.get_error() == "colmap, InterfaceCOLMAP, TextureMesh not found: run ./setup-deps.sh"
    )


def test_openmvs_tools_are_found_in_their_install_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "OpenMVS"
    folder.mkdir()
    tool = folder / "TextureMesh"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(colmap_backend, "OPENMVS_DIR", folder)

    assert colmap_backend.find_tool("TextureMesh") == str(tool)
    assert colmap_backend.missing_tools() == ["colmap", "InterfaceCOLMAP"]


def test_stopping_kills_the_running_tool_and_reports_cancelled(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "slow")
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    deadline = time.monotonic() + 15
    while engine.get_progress() == 0.0:  # wait until the stand-in has printed its first lines
        assert time.monotonic() < deadline
        time.sleep(0.02)

    started = time.monotonic()
    engine.stop_reconstruction()
    wait_for_end(engine, timeout=10)

    assert time.monotonic() - started < 5  # not the 60 s the stand-in wanted to sleep
    assert engine.get_state() == State.CANCELLED and engine.get_progress() == 0.0
    assert not (tmp_path / "out.glb").exists()


def test_a_new_run_starts_from_a_clean_state(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "fail")
    engine = make_engine(tmp_path)
    engine.start_reconstruction()
    wait_for_end(engine)
    assert engine.get_state() == State.FAILED

    monkeypatch.setenv("FAKE_MODE", "ok")
    engine.start_reconstruction()
    wait_for_end(engine)
    assert engine.get_state() == State.DONE and engine.get_error() is None


# -- progress from log lines ---------------------------------------------------------------------


def test_progress_follows_the_stages_and_never_goes_backwards() -> None:
    tracker = colmap_backend.ProgressTracker(photos=10)
    seen = [
        tracker.feed(line)
        for line in (
            "unrelated line",
            "Extracting features",
            "Processed file [5/10]",
            "Matching block [1/1, 1/1]",
            "Registering image #7 (3)",
            "Extracting features",  # an earlier stage again
            "Processing view 4 / 4 for a.jpg",  # patch_match_stereo also contains "match"
            "Fusing",
        )
    ]
    assert seen[0] == 0.0
    assert seen == sorted(seen)
    assert seen[2] > seen[1]  # half the files processed is further than none
    assert seen[5] == seen[4]  # a stage that is already past is ignored
    assert seen[6] > seen[5]
    assert 0.0 < seen[-1] < 1.0


def test_openmvs_stages_are_entered_by_name() -> None:
    tracker = colmap_backend.ProgressTracker(photos=3)
    tracker.feed("Poisson reconstruction")
    before = tracker.progress
    tracker.enter("openmvs")
    middle = tracker.progress
    tracker.enter("texturing")
    assert before < middle < tracker.progress < 1.0
    tracker.enter("openmvs")  # entering a stage already passed does nothing
    assert tracker.stage == len(colmap_backend.STAGES) - 1


def test_glog_prefixes_are_cut_from_error_lines() -> None:
    from collections import deque

    tail = deque(["E20260512 08:30:15.123456 4242 mapper.cc:77] Check failed: num_images > 1"])
    assert colmap_backend._explain("/x/colmap", 134, tail) == (
        "colmap exited with code 134: Check failed: num_images > 1"
    )


def test_trimesh_reads_back_a_box_made_here(tmp_path: Path) -> None:
    path = tmp_path / "box.glb"
    trimesh.creation.box(extents=(2, 1, 0.5)).export(path)
    low, high = trimesh.load(path, process=False).bounds
    assert tuple(high - low) == pytest.approx((2.0, 1.0, 0.5))
