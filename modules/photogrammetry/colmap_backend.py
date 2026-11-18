"""Photogrammetry on Linux: COLMAP builds the geometry, OpenMVS paints the photos onto it.

    photos -> colmap automatic_reconstructor   (camera poses, dense points, Poisson mesh; CUDA)
           -> InterfaceCOLMAP                  (COLMAP's output as an OpenMVS scene)
           -> TextureMesh                      (the textured model, a .glb)

Each tool runs as a subprocess, like ``gst-launch-1.0`` does for video. The pipeline runs on a
worker thread, so ``start_reconstruction`` returns at once and the module polls, the same way
it polls RealityKit. COLMAP has no progress API; progress is read from its log (see
``ProgressTracker``) and is only as fine-grained as that log.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import trimesh

from .pgm import BoundingBox, State

log = logging.getLogger(__name__)

OPENMVS_DIR = Path("/usr/local/bin/OpenMVS")  # where `cmake --install` puts OpenMVS's tools
MIN_PHOTOS = 3
KILL_GRACE = 5.0  # seconds a tool gets to stop after SIGTERM before it is killed
ERROR_WORDS = re.compile(r"error|fail|requires|cannot|not found|check failed", re.IGNORECASE)

# RealityKit's five detail levels (0..4) onto COLMAP's four quality presets.
QUALITY = {0: "low", 1: "low", 2: "medium", 3: "high", 4: "extreme"}


def find_tool(name: str) -> str | None:
    """Path of an executable on PATH, or in OpenMVS's install folder."""
    found = shutil.which(name)
    if found:
        return found
    candidate = OPENMVS_DIR / name
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def missing_tools() -> list[str]:
    return [name for name in ("colmap", "InterfaceCOLMAP", "TextureMesh") if not find_tool(name)]


# -- progress ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    name: str
    weight: float  # share of the whole run; rough
    marker: re.Pattern[str]  # a log line that means "this stage is running"
    count: re.Pattern[str] | None = None  # a log line with "item (done) of (total)" groups


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# In pipeline order. The patterns are matched against COLMAP's and OpenMVS's log lines; a line
# that matches none of them is ignored, so a wrong pattern shows up as progress that jumps
# rather than as a failure. The log of every run is kept in the workspace to adjust them.
STAGES = (
    Stage(
        "features",
        0.08,
        _p(r"extract.*feature|feature.*extract"),
        _p(r"Processed file \[(\d+)/(\d+)\]"),
    ),
    Stage("matching", 0.10, _p(r"match|vocab")),
    Stage("sparse", 0.12, _p(r"registering image|bundle adjustment|incremental|mapper")),
    Stage("undistort", 0.05, _p(r"undistort")),
    Stage(
        "stereo",
        0.40,
        _p(r"patch.?match|stereo|processing view"),
        _p(r"Processing view (\d+) / (\d+)"),
    ),
    Stage("fusion", 0.05, _p(r"fusion|fusing|fused")),
    Stage("meshing", 0.05, _p(r"poisson|delaunay|meshing")),
    Stage("openmvs", 0.05, _p(r"$^")),  # entered explicitly: see ProgressTracker.enter
    Stage("texturing", 0.10, _p(r"$^")),
)
# "Registering image #4 (3)": the number in brackets is how many are registered so far.
REGISTERED = _p(r"Registering image #\d+ \((\d+)\)")


class ProgressTracker:
    """Turns log lines into a 0..1 progress. Never goes backwards."""

    def __init__(self, photos: int) -> None:
        self.photos = max(1, photos)
        self.stage = -1
        self.within = 0.0

    def feed(self, line: str) -> float:
        for index in range(len(STAGES) - 1, self.stage, -1):  # the latest stage that matches
            if STAGES[index].marker.search(line):
                self.stage, self.within = index, 0.0
                break
        if self.stage >= 0:
            self.within = max(self.within, self._fraction(STAGES[self.stage], line))
        return self.progress

    def enter(self, name: str) -> None:
        """Move to a stage that has no log line to recognise it by."""
        index = next(i for i, s in enumerate(STAGES) if s.name == name)
        if index > self.stage:
            self.stage, self.within = index, 0.0

    def _fraction(self, stage: Stage, line: str) -> float:
        if stage.name == "sparse" and (m := REGISTERED.search(line)):
            return min(1.0, int(m[1]) / self.photos)
        if stage.count and (m := stage.count.search(line)) and int(m[2]) > 0:
            return min(1.0, int(m[1]) / int(m[2]))
        return 0.0

    @property
    def progress(self) -> float:
        if self.stage < 0:
            return 0.0
        done = sum(s.weight for s in STAGES[: self.stage])
        total = sum(s.weight for s in STAGES)
        return min(1.0, (done + STAGES[self.stage].weight * self.within) / total)


# -- the engine --------------------------------------------------------------------------------


class _Failed(Exception):
    """A step failed; the message is what the UI shows."""


class _Cancelled(Exception):
    pass


class ColmapPhotogrammetry:
    """Same interface as ``pgm.Photogrammetry`` (see ``engine.Engine``)."""

    def __init__(
        self,
        *,
        detail: int = 0,
        temp_dir: str = "pgm-temp/",
        output_path: str = "output.glb",
        **_: object,  # the RealityKit engine's video_fps and target_fps; capture.py replaces both
    ) -> None:
        self.detail = detail
        self.temp_dir = temp_dir
        self.output_path = output_path
        self.frames_dir = os.path.join(temp_dir, "reconstruction")
        self.workspace = Path(temp_dir) / "colmap"

        self._lock = threading.Lock()
        self._state = State.IDLE
        self._progress = 0.0
        self._error: str | None = None
        self._cancel = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    # -- the interface the module uses ----------------------------------------------------

    def start_reconstruction(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            self._state, self._progress, self._error = State.RUNNING, 0.0, None
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="colmap", daemon=True)
        self._thread.start()

    def stop_reconstruction(self) -> None:
        """Ask the running tool to stop; returns at once, the worker thread tidies up."""
        self._cancel.set()
        with self._lock:
            process = self._process
        if process is not None:
            _signal(process, signal.SIGTERM)
            killer = threading.Timer(KILL_GRACE, _signal, (process, signal.SIGKILL))
            killer.daemon = True
            killer.start()

    def is_completed(self) -> bool:
        return self.get_state() >= State.DONE

    def get_state(self) -> State:
        with self._lock:
            return self._state

    def get_progress(self) -> float:
        with self._lock:
            return self._progress

    def get_eta(self) -> float:
        return 0.0  # COLMAP gives nothing to estimate from

    def get_error(self) -> str | None:
        with self._lock:
            return self._error

    def get_bounding_box(self, scale_factor: float) -> BoundingBox:
        """The model's bounding box, read from the .glb itself so it matches what is viewed."""
        low, high = trimesh.load(self.output_path, process=False).bounds * scale_factor
        return BoundingBox(*low, *high)

    # -- the pipeline ---------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._pipeline()
        except _Cancelled:
            self._finish(State.CANCELLED, None)
        except _Failed as failure:
            self._finish(State.FAILED, str(failure))
        except Exception as error:  # a bug here must not leave the module waiting forever
            log.exception("photogrammetry pipeline crashed")
            self._finish(State.FAILED, f"{type(error).__name__}: {error}")
        else:
            self._finish(State.DONE, None)

    def _finish(self, state: State, error: str | None) -> None:
        with self._lock:
            self._state, self._error = state, error
            self._progress = 1.0 if state == State.DONE else 0.0

    def _pipeline(self) -> None:
        if missing := missing_tools():
            raise _Failed(f"{', '.join(missing)} not found: run ./setup-deps.sh")
        colmap, interface, texture = (
            find_tool(n) for n in ("colmap", "InterfaceCOLMAP", "TextureMesh")
        )
        assert colmap and interface and texture

        photos = (
            sorted(Path(self.frames_dir).glob("*.jpg")) if os.path.isdir(self.frames_dir) else []
        )
        if len(photos) < MIN_PHOTOS:
            raise _Failed(f"Only {len(photos)} photos: at least {MIN_PHOTOS} are needed")

        work = self.workspace
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        tracker = ProgressTracker(len(photos))
        log_file = work.with_name("colmap.log")  # outside the workspace COLMAP fills
        with log_file.open("w") as log_out:

            def run(args: list[str]) -> None:
                self._run_tool(args, tracker, log_out)

            # Every photo is from the one camera, which makes the camera model far better fixed.
            run([
                colmap, "automatic_reconstructor",
                "--workspace_path", str(work),
                "--image_path", self.frames_dir,
                "--quality", QUALITY[self.detail],
                "--single_camera", "1",
                "--dense", "1",
            ])  # fmt: skip
            dense = work / "dense" / "0"
            mesh = dense / "meshed-poisson.ply"
            if not mesh.is_file():
                raise _Failed("COLMAP made no mesh: the photos probably did not overlap enough")

            scene, textured = work / "scene.mvs", work / "textured.mvs"
            tracker.enter("openmvs")
            run(
                [
                    interface,
                    "-i",
                    str(dense),
                    "-o",
                    str(scene),
                    "--image-folder",
                    str(dense / "images"),
                    "-w",
                    str(work),
                ]
            )
            tracker.enter("texturing")
            run(
                [
                    texture,
                    str(scene),
                    "-m",
                    str(mesh),
                    "-o",
                    str(textured),
                    "--export-type",
                    "glb",
                    "-w",
                    str(work),
                ]
            )

        models = sorted(work.glob("textured*.glb"))
        if not models:
            raise _Failed("TextureMesh finished but wrote no .glb")
        out = Path(self.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(models[0], out)

    def _run_tool(self, args: list[str], tracker: ProgressTracker, log_out: TextIO) -> None:
        if self._cancel.is_set():
            raise _Cancelled
        log_out.write(f"$ {' '.join(args)}\n")
        tail: deque[str] = deque(maxlen=50)
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,  # its own process group, so cancelling reaches its children
        )
        with self._lock:
            self._process = process
        if self._cancel.is_set():  # cancelled between the check above and the process existing
            _terminate(process)
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip()
                log_out.write(line + "\n")
                if line.strip():
                    tail.append(line)
                progress = tracker.feed(line)
                with self._lock:
                    self._progress = max(self._progress, progress)
            code = process.wait()
        finally:
            with self._lock:
                self._process = None
            if process.poll() is None:
                _terminate(process)
        if self._cancel.is_set():
            raise _Cancelled
        if code != 0:
            raise _Failed(_explain(args[0], code, tail))


def _explain(tool: str, code: int, tail: deque[str]) -> str:
    """One line for the UI: the last line of the tool's output that looks like the reason."""
    reason = next((line for line in reversed(tail) if ERROR_WORDS.search(line)), None)
    if reason is None and tail:
        reason = tail[-1]
    # COLMAP's log lines start with a glog prefix such as "E20260101 12:00:00.000000 12 file.cc:1]".
    reason = re.sub(r"^[IWEF]\d{8} [\d:.]+ +\d+ \S+\] ", "", reason or "no output").strip()
    return f"{os.path.basename(tool)} exited with code {code}: {reason}"


def _signal(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    """Send a signal to a tool's whole process group (the tool and its children)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


def _terminate(process: subprocess.Popen[str]) -> None:
    """Stop a tool and its children: SIGTERM, then SIGKILL if it lingers (up to KILL_GRACE)."""
    _signal(process, signal.SIGTERM)
    try:
        process.wait(KILL_GRACE)
    except subprocess.TimeoutExpired:
        _signal(process, signal.SIGKILL)
