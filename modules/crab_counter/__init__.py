"""Crab counter: mate-26's SIFT tracker, started from the UI and fed by the camera stream.

Start opens the link to the camera; Stop closes it. While counting, the module publishes
its own annotated stream (named after the module) that you can pick in the video panel.
"""

import asyncio
import os
import time

import cv2
import numpy as np

from rovkit import Button, Module, Readout, Slider

from .monkeyseecrab import MultiCrabTracker

REFERENCE_IMAGE = os.path.join(os.path.dirname(__file__), "assets", "monkeydo.png")
NO_FRAME_AFTER = 1.0  # seconds without a frame before the status says so


class CrabCounter(Module):
    title = "Crab counter"

    start_counter = Button("Start Counter")
    stop_counter = Button("Stop Counter")
    reset_counter = Button("Reset Counter")
    status = Readout("Status", default="Stopped")
    visible = Readout("In frame", default=0)
    total = Readout("Total crabs", default=0)
    output_fps = Readout("Output FPS", unit="fps", default=0)

    # The tracker settings mate-26 had on its Dev Tools window.
    min_present = Slider(1, 20, step=1, default=3, label="Min frames present")
    distance_thresh = Slider(10, 500, step=1, default=150, unit="px", label="Match distance")
    missing_frames = Slider(1, 100, step=1, default=25, label="Frames until lost")
    recount_cooldown = Slider(0, 300, step=1, default=60, label="Recount cooldown")
    detection_interval = Slider(1, 30, step=1, default=3, label="Detect every N frames")

    tracker: MultiCrabTracker
    _task: asyncio.Task[None] | None = None
    _reset_requested = False

    async def on_start(self) -> None:
        self.tracker = MultiCrabTracker()
        if not self.tracker.load_training_image(REFERENCE_IMAGE):
            self.log.error("could not use the reference image %s", REFERENCE_IMAGE)
            self.status = "Reference image failed to load"
        self.tracker.counting = False

    async def on_stop(self) -> None:
        await self._halt()

    async def on_start_counter(self) -> None:
        if self._task is None or self._task.done():
            self._task = self.spawn(self._count(), "count")

    async def on_stop_counter(self) -> None:
        await self._halt()

    async def on_reset_counter(self) -> None:
        self._reset_requested = True
        if self._task is None or self._task.done():  # nothing is using the tracker right now
            self._apply_reset()
            self._show_counts()

    # -- counting ------------------------------------------------------------------------

    async def _count(self) -> None:
        self.tracker.counting = True
        self._update("status", "Waiting for video…")
        processed, since = 0, time.monotonic()
        try:
            async with self.frames() as frames:
                out = self.video(self.id)
                try:
                    while True:
                        try:
                            jpeg = await frames.next(NO_FRAME_AFTER)
                        except TimeoutError:
                            self._update("status", "Waiting for video…")
                            continue
                        self._update("status", "Counting…")
                        out.push_jpeg(await asyncio.to_thread(self._process, jpeg))
                        self._show_counts()
                        processed += 1
                        if (elapsed := time.monotonic() - since) >= 1.0:
                            self._update("output_fps", round(processed / elapsed, 1))
                            processed, since = 0, time.monotonic()
                finally:
                    await out.close()
        finally:
            self.tracker.counting = False
            self._update("status", "Stopped")
            self._update("output_fps", 0)

    def _process(self, jpeg: bytes) -> bytes:
        """Runs in a worker thread: decode, detect and track, draw the boxes, re-encode."""
        if self._reset_requested:
            self._apply_reset()
        tracker = self.tracker
        tracker.min_present = int(self.min_present)
        tracker.distance_thresh = int(self.distance_thresh)
        tracker.max_missing = int(self.missing_frames)
        tracker.recount_cooldown = int(self.recount_cooldown)
        tracker.detection_interval = int(self.detection_interval)

        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return jpeg  # not decodable; pass it through untouched
        return cv2.imencode(".jpg", tracker.detect(frame))[1].tobytes()

    def _apply_reset(self) -> None:
        self._reset_requested = False
        self.tracker.reset_counter()

    async def _halt(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _show_counts(self) -> None:
        self._update("total", self.tracker.counter)
        self._update("visible", self.tracker.visible_count)

    def _update(self, widget: str, value: object) -> None:
        """Assign a readout only when it changed, so the hub isn't flooded at frame rate."""
        if getattr(self, widget) != value:
            setattr(self, widget, value)
