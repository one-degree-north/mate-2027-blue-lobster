"""Photogrammetry: Apple RealityKit (through ``pgm``), driven from the UI.

Start Photogrammetry opens the link to the camera and saves photos; Begin Reconstruction
closes it and builds the 3D model. Calibrate sets the scale from an object of known size.
macOS only: build the Swift library first (``just build-pgm``).
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from rovkit import Button, Module, Number, Progress, Readout, Select, Slider, Status

from .capture import Capture
from .pgm import DYLIB_PATH, BoundingBox, Photogrammetry, State

NO_FRAME_AFTER = 1.0  # seconds without a frame before the status says so
POLL_SECONDS = 0.5  # how often reconstruction progress is read
SCALE_MIN, SCALE_MAX = 1e-6, 1e6

Detail = Literal[0, 1, 2, 3, 4]
DETAIL_LEVELS: dict[str, Detail] = {"preview": 0, "reduced": 1, "medium": 2, "full": 3, "raw": 4}
DETAIL_LABELS = {
    "preview": "Preview (fastest)",
    "reduced": "Reduced",
    "medium": "Medium",
    "full": "Full",
    "raw": "Raw (slowest)",
}
AXES = {
    "longest": "Longest side",
    "width": "Width (X)",
    "height": "Height (Y)",
    "depth": "Depth (Z)",
}

# Every widget that can be switched off while the module is busy.
CONTROLS = (
    "capture_hz",
    "detail",
    "start_photogrammetry",
    "begin_reconstruction",
    "cancel",
    "open_model",
    "calibrate",
    "get_bounding_box",
)
# What is switched off in each phase. Idle is "everything except Begin and Cancel".
LOCKED: dict[str, set[str]] = {
    "idle": {"begin_reconstruction", "cancel"},
    "capturing": {"capture_hz", "detail", "start_photogrammetry"},
    "reconstructing": {
        "capture_hz",
        "detail",
        "start_photogrammetry",
        "begin_reconstruction",
        "open_model",
        "calibrate",
        "get_bounding_box",
    },
}

Phase = Literal["idle", "capturing", "reconstructing"]


def unsupported_reason() -> str | None:
    """Why photogrammetry cannot run here, or None."""
    if sys.platform != "darwin":
        return "Photogrammetry needs macOS (RealityKit)"
    if not os.path.exists(DYLIB_PATH):
        return "libPgm.dylib is missing: run `just build-pgm`"
    return None


def measured_length(box: BoundingBox, axis: str) -> float:
    """The model's size along ``axis`` (one of AXES), in the model's own units."""
    sizes = {"width": box.width, "height": box.height, "depth": box.depth}
    return max(sizes.values()) if axis == "longest" else sizes[axis]


class PhotogrammetryModule(Module):
    title = "Photogrammetry"

    capture_hz = Slider(0.2, 10, step=0.1, default=2.0, unit="photos/s", label="Photos per second")
    detail = Select(DETAIL_LABELS, default="preview", label="Detail level")

    start_photogrammetry = Button("Start Photogrammetry")
    begin_reconstruction = Button("Begin Reconstruction")
    cancel = Button("Cancel")
    status = Status("Status", default="Idle")
    photos = Readout("Photos captured", default=0)
    progress = Progress("Reconstruction")
    eta = Readout("Time remaining", unit="s")
    open_model = Button("Open Model in Preview")

    # Scale: type a factor, or measure it from an object of known size.
    reference_length = Number(0, 100000, default=0, label="Reference length")
    reference_axis = Select(AXES, default="longest", label="Reference measured along")
    calibrate = Button("Use Model as Reference")
    scale_factor = Number(SCALE_MIN, SCALE_MAX, default=1.0, label="Scale factor")
    get_bounding_box = Button("Get Bounding Box")
    size = Readout("Bounding box size")
    corners = Readout("Min → max corner")

    pgm: Photogrammetry | None = None
    _phase: Phase = "idle"
    _capture: Capture | None = None
    _capture_task: asyncio.Task[None] | None = None
    _reconstruction: asyncio.Task[None] | None = None
    _raw_box: BoundingBox | None = None  # the model's bounding box before scaling

    async def on_start(self) -> None:
        reason = unsupported_reason()
        if reason is not None:
            self.update("status", (reason, "error"))
        else:
            self.pgm = self._make_engine()
        self._set_phase("idle")

    @staticmethod
    def _make_engine() -> Photogrammetry:
        return Photogrammetry(
            video_fps=30,  # only used by pgm's own frame counting, which capture.py replaces
            target_fps=2,
            detail=0,
            temp_dir=os.path.abspath("pgm-temp"),
            output_path=os.path.abspath("output.usdz"),
        )

    async def on_stop(self) -> None:
        await self._cancel(self._capture_task)
        await self._cancel(self._reconstruction)  # also asks RealityKit to stop

    # -- phases ----------------------------------------------------------------------------

    def _set_phase(self, phase: Phase) -> None:
        """Switch the controls to match what the module is doing."""
        self._phase = phase
        locked = set(CONTROLS) if self.pgm is None else LOCKED[phase]
        self.enable(*(name for name in CONTROLS if name not in locked))
        self.disable(*(name for name in CONTROLS if name in locked))

    # -- capture ---------------------------------------------------------------------------

    async def on_start_photogrammetry(self) -> None:
        if self.pgm is None or self._phase != "idle":
            return
        self._capture = Capture(Path(self.pgm.frames_dir), self.capture_hz)
        await asyncio.to_thread(self._capture.start)
        self.update("photos", 0)
        self.update("progress", 0.0)
        self.update("eta", None)
        self.update("status", "Capturing…")
        self._set_phase("capturing")
        self._capture_task = self.spawn(self._capture_photos(self._capture), "capture")

    async def _capture_photos(self, capture: Capture) -> None:
        try:
            async with self.frames() as frames:
                while True:
                    try:
                        jpeg = await frames.next(NO_FRAME_AFTER)
                    except TimeoutError:
                        self.update("status", ("Capturing… (waiting for video)", "warn"))
                        continue
                    self.update("status", "Capturing…")
                    self.update("photos", await asyncio.to_thread(capture.offer, jpeg))
        except Exception as error:
            self.update("status", (f"Capture failed: {error}", "error"))
            self._set_phase("idle")
            raise

    async def on_begin_reconstruction(self) -> None:
        if self.pgm is None or self._phase != "capturing" or self._capture is None:
            return
        await self._cancel(self._capture_task)  # closes the link to the camera
        count = await asyncio.to_thread(self._capture.finish)
        self.update("photos", count)
        if count == 0:
            self.update("status", ("No photos were captured", "error"))
            self._set_phase("idle")
            return
        self.pgm.detail = DETAIL_LEVELS[self.detail]
        self._set_phase("reconstructing")
        self._reconstruction = self.spawn(self._reconstruct(self.pgm), "reconstruct")

    # -- reconstruction --------------------------------------------------------------------

    async def _reconstruct(self, pgm: Photogrammetry) -> None:
        self.update("status", "Reconstructing…")
        self.update("progress", 0.0)
        try:
            await asyncio.to_thread(pgm.start_reconstruction)
            while not pgm.is_completed():
                self.update("progress", pgm.get_progress())
                eta = pgm.get_eta()
                self.update("eta", round(eta) if eta > 0 else None)
                await asyncio.sleep(POLL_SECONDS)
            self._raw_box = None  # the old model is gone
            state = pgm.get_state()
            if state == State.DONE:
                self.update("progress", 1.0)
                self.update("status", ("Done", "ok"))
            elif state == State.CANCELLED:
                self.update("progress", 0.0)
                self.update("status", ("Cancelled", "warn"))
            else:
                reason = pgm.get_error() or "no details"
                self.update("status", (f"Reconstruction failed: {reason}", "error"))
        except asyncio.CancelledError:
            pgm.stop_reconstruction()  # the module is stopping: don't leave RealityKit running
            raise
        except Exception as error:
            self.update("status", (f"Reconstruction failed: {error}", "error"))
            raise
        finally:
            self.update("eta", None)
            self._set_phase("idle")

    async def on_cancel(self) -> None:
        if self.pgm is None:
            return
        if self._phase == "capturing":
            await self._cancel(self._capture_task)
            if self._capture is not None:
                await asyncio.to_thread(self._capture.finish)
            self.update("status", ("Capture cancelled", "warn"))
            self._set_phase("idle")
        elif self._phase == "reconstructing":
            self.pgm.stop_reconstruction()  # _reconstruct sees the cancelled state and tidies up

    # -- the finished model ----------------------------------------------------------------

    async def on_open_model(self) -> None:
        if self.pgm is None or not self._model_exists():
            self.update("status", ("There is no model yet", "warn"))
            return
        await asyncio.to_thread(subprocess.run, ["open", "-a", "Preview", self.pgm.output_path])

    async def on_get_bounding_box(self) -> None:
        if self.pgm is None or not self._model_exists():
            self.update("status", ("There is no model yet", "warn"))
            return
        self._raw_box = await asyncio.to_thread(self.pgm.get_bounding_box, 1.0)
        self._show_box()

    async def on_scale_factor(self, value: float) -> None:
        self._show_box()  # the numbers on screen follow the factor

    async def on_calibrate(self) -> None:
        """Set the scale so that the model's size along an axis matches a known length."""
        if self.pgm is None or not self._model_exists():
            self.update("status", ("There is no model yet", "warn"))
            return
        if self.reference_length <= 0:
            self.update("status", ("Enter the reference length first", "warn"))
            return
        box = await asyncio.to_thread(self.pgm.get_bounding_box, 1.0)
        measured = measured_length(box, self.reference_axis)
        if measured <= 0:
            self.update("status", ("The model has no size along that axis", "error"))
            return
        factor = self.reference_length / measured
        if not SCALE_MIN <= factor <= SCALE_MAX:
            self.update("status", (f"Scale factor {factor:.3g} is out of range", "error"))
            return
        self._raw_box = box
        self.scale_factor = factor
        self._show_box()
        self.update("status", (f"Calibrated: scale factor {factor:.6g}", "ok"))

    def _show_box(self) -> None:
        box = self._raw_box
        if box is None:
            return
        k = self.scale_factor
        self.update(
            "size", f"W: {box.width * k:.4f}  H: {box.height * k:.4f}  D: {box.depth * k:.4f}"
        )
        self.update(
            "corners",
            f"({box.min_x * k:.3f}, {box.min_y * k:.3f}, {box.min_z * k:.3f}) → "
            f"({box.max_x * k:.3f}, {box.max_y * k:.3f}, {box.max_z * k:.3f})",
        )

    # -- helpers ---------------------------------------------------------------------------

    def _model_exists(self) -> bool:
        return self.pgm is not None and os.path.exists(self.pgm.output_path)

    @staticmethod
    async def _cancel(task: asyncio.Task[None] | None) -> None:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
