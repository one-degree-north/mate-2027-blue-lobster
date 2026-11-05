"""Saving photos for photogrammetry from a stream of JPEG frames.

mate-26 kept every Nth frame, with N worked out from the source's fps. That is wrong whenever
the source runs at another rate, or the module is too busy to see every frame. Here a photo is
taken every ``1 / hz`` seconds of real time, and the sharpest frame of that interval is the
one kept: motion blur is the commonest reason a photogrammetry reconstruction fails.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np


def sharpness(jpeg: bytes) -> float:
    """How sharp a JPEG is: the variance of its Laplacian. Higher is sharper.

    Decoded at half size, which is quicker and measures sharpness just as well.
    """
    gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_2)
    if gray is None:
        return -1.0  # not decodable: worse than any real frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class Capture:
    """Writes ``frame_00001.jpg``, ``frame_00002.jpg``, ... into ``folder``.

    The names are zero-padded so that sorting them as text keeps the capture order, which
    matters because RealityKit is told the photos are in sequence. Thread-safe: ``offer`` runs
    in worker threads, and a cancelled task can leave one still running when ``finish`` is called.
    """

    def __init__(
        self,
        folder: Path,
        hz: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        measure: Callable[[bytes], float] = sharpness,
    ) -> None:
        if hz <= 0:
            raise ValueError("hz must be positive")
        self.folder = folder
        self.interval = 1.0 / hz
        self._clock = clock
        self._measure = measure
        self._lock = threading.Lock()
        self._count = 0
        self._closed = False
        self._window_start: float | None = None
        self._best: bytes | None = None
        self._best_score = float("-inf")

    @property
    def count(self) -> int:
        return self._count

    def start(self) -> None:
        """Empty the folder, so the reconstruction only sees this capture's photos."""
        with self._lock:
            if self.folder.exists():
                shutil.rmtree(self.folder)
            self.folder.mkdir(parents=True)

    def offer(self, jpeg: bytes) -> int:
        """Consider one frame; returns how many photos have been saved so far."""
        score = self._measure(jpeg)  # the slow part, so outside the lock
        with self._lock:
            if self._closed:
                return self._count
            now = self._clock()
            if self._window_start is None:
                self._window_start = now
            if score > self._best_score:
                self._best, self._best_score = jpeg, score
            if now - self._window_start >= self.interval:
                self._save_best()
                # Advance by exactly one interval so the long-run rate is exact even when
                # frames arrive at awkward times; after a long gap, start over from now.
                self._window_start += self.interval
                if now - self._window_start >= self.interval:
                    self._window_start = now
            return self._count

    def finish(self) -> int:
        """Save the best frame of the unfinished interval; later frames are ignored.

        Returns the total number of photos.
        """
        with self._lock:
            if not self._closed:
                self._closed = True
                self._save_best()
            return self._count

    def _save_best(self) -> None:
        if self._best is None:
            return
        self._count += 1
        (self.folder / f"frame_{self._count:05d}.jpg").write_bytes(self._best)
        self._best, self._best_score = None, float("-inf")
