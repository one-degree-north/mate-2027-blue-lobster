"""Photogrammetry: mate-26's pgm package (Apple RealityKit), driven from the UI.

Start Photogrammetry opens the link to the camera and saves photos; Begin Reconstruction
closes it and builds the 3D model. macOS only: build the Swift library first (``just build-pgm``).
"""

import asyncio
import os
import subprocess
import sys
from typing import Literal, cast

import cv2
import numpy as np

from rovkit import Button, Module, Progress, Readout, Slider

from .pgm import DYLIB_PATH, Photogrammetry

Detail = Literal[0, 1, 2, 3, 4]
NO_FRAME_AFTER = 1.0  # seconds without a frame before the status says so


class PhotogrammetryModule(Module):
    title = "Photogrammetry"

    capture_hz = Slider(0.2, 10, step=0.1, default=2.0, unit="photos/s", label="Photos per second")
    video_fps = Slider(1, 60, step=1, default=30, unit="fps", label="Video FPS")
    detail = Slider(0, 4, step=1, default=0, label="Detail level")

    start_photogrammetry = Button("Start Photogrammetry")
    begin_reconstruction = Button("Begin Reconstruction")
    status = Readout("Status", default="Idle")
    photos = Readout("Photos captured", default=0)
    progress = Progress("Reconstruction")
    eta = Readout("Time remaining", unit="s")
    open_model = Button("Open Model in Preview")

    scale_factor = Slider(0.1, 10, step=0.01, default=1.0, label="Scale factor")
    get_bounding_box = Button("Get Bounding Box")
    size = Readout("Bounding box size")
    corners = Readout("Min → max corner")

    pgm: Photogrammetry | None = None
    _capture: asyncio.Task[None] | None = None
    _reconstruction: asyncio.Task[None] | None = None

    async def on_start(self) -> None:
        self._load()

    def _load(self) -> None:
        """Create the Photogrammetry engine, or say in the status why that is not possible."""
        if sys.platform != "darwin":
            self.status = "Photogrammetry needs macOS (RealityKit)"
        elif not os.path.exists(DYLIB_PATH):
            self.status = "libPgm.dylib is missing: run `just build-pgm`"
        else:
            self.pgm = Photogrammetry(
                video_fps=int(self.video_fps),
                target_fps=int(self.capture_hz),
                detail=cast(Detail, int(self.detail)),
                temp_dir=os.path.abspath("pgm-temp"),
                output_path=os.path.abspath("output.usdz"),
            )
            self.pgm.set_capture_rate(self.capture_hz)

    def _model_exists(self) -> bool:
        return self.pgm is not None and os.path.exists(self.pgm.output_path)

    async def on_stop(self) -> None:
        await self._cancel(self._capture)
        await self._cancel(self._reconstruction)

    # -- settings: locked while capturing, like mate-26's greyed-out sliders ---------------

    async def on_capture_hz(self, value: float) -> None:
        if self.pgm is None:
            return
        if self._is_capturing():
            self.capture_hz = self.pgm.target_fps
        else:
            self.pgm.set_capture_rate(value)

    async def on_video_fps(self, value: float) -> None:
        if self.pgm is None:
            return
        if self._is_capturing():
            self.video_fps = self.pgm.video_fps
        else:
            self.pgm.video_fps = int(value)

    async def on_detail(self, value: float) -> None:
        if self.pgm is None:
            return
        if self._is_capturing():
            self.detail = self.pgm.detail
        else:
            self.pgm.detail = cast(Detail, int(value))

    # -- capture and reconstruction --------------------------------------------------------

    async def on_start_photogrammetry(self) -> None:
        if self.pgm is None or self._is_capturing() or self._is_reconstructing():
            return
        self.pgm.start_recording()
        self.photos = 0
        self.progress = 0.0
        self._update("eta", None)
        self._capture = self.spawn(self._capture_photos(), "capture")

    async def on_begin_reconstruction(self) -> None:
        if self.pgm is None or not self._is_capturing():
            return
        await self._cancel(self._capture)  # closes the link to the camera
        self.pgm.stop_recording()
        if not self.photos:
            self.status = "No photos were captured"
            return
        self._reconstruction = self.spawn(self._reconstruct(), "reconstruct")

    async def _capture_photos(self) -> None:
        self._update("status", "Capturing…")
        async with self.frames() as frames:
            while True:
                try:
                    jpeg = await frames.next(NO_FRAME_AFTER)
                except TimeoutError:
                    self._update("status", "Capturing… (waiting for video)")
                    continue
                self._update("status", "Capturing…")
                self._update("photos", await asyncio.to_thread(self._save, jpeg))

    def _save(self, jpeg: bytes) -> int:
        """Runs in a worker thread. Returns how many photos are saved so far."""
        assert self.pgm is not None
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            self.pgm.receive_frame(frame)
        return len(os.listdir(self.pgm.frames_dir))

    async def _reconstruct(self) -> None:
        assert self.pgm is not None
        self.status = "Reconstructing…"
        self.progress = 0.0
        try:
            await asyncio.to_thread(self.pgm.start_reconstruction)
            while not self.pgm.is_completed():
                self.progress = self.pgm.get_progress()
                eta = self.pgm.get_eta()
                self._update("eta", round(eta) if eta > 0 else None)
                await asyncio.sleep(0.5)
        except Exception as error:
            self.status = f"Reconstruction failed: {error}"
            raise
        self.progress = 1.0
        self._update("eta", None)
        self.status = "Done"

    # -- the finished model ----------------------------------------------------------------

    async def on_open_model(self) -> None:
        if self.pgm is None or not self._model_exists():
            self.status = "There is no model yet"
            return
        await asyncio.to_thread(subprocess.run, ["open", "-a", "Preview", self.pgm.output_path])

    async def on_get_bounding_box(self) -> None:
        if self.pgm is None or not self._model_exists():
            self.status = "There is no model yet"
            return
        box = await asyncio.to_thread(self.pgm.get_bounding_box, self.scale_factor)
        self.size = f"W: {box.width:.4f}  H: {box.height:.4f}  D: {box.depth:.4f}"
        self.corners = (
            f"({box.min_x:.3f}, {box.min_y:.3f}, {box.min_z:.3f}) → "
            f"({box.max_x:.3f}, {box.max_y:.3f}, {box.max_z:.3f})"
        )

    # -- helpers ---------------------------------------------------------------------------

    def _is_capturing(self) -> bool:
        return self._capture is not None and not self._capture.done()

    def _is_reconstructing(self) -> bool:
        return self._reconstruction is not None and not self._reconstruction.done()

    @staticmethod
    async def _cancel(task: asyncio.Task[None] | None) -> None:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _update(self, widget: str, value: object) -> None:
        """Assign a readout only when it changed."""
        if getattr(self, widget) != value:
            setattr(self, widget, value)
