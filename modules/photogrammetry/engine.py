"""What the photogrammetry module needs from a reconstruction engine.

Two engines implement it: ``pgm.Photogrammetry`` (Apple RealityKit, macOS) and
``colmap_backend.ColmapPhotogrammetry`` (COLMAP and OpenMVS, Linux with an NVIDIA GPU).
"""

from __future__ import annotations

from typing import Literal, Protocol

from .pgm import BoundingBox, State

Detail = Literal[0, 1, 2, 3, 4]


class Engine(Protocol):
    detail: Detail
    frames_dir: str  # the photos to reconstruct from are written here
    output_path: str  # where the finished model is written

    def start_reconstruction(self) -> None:
        """Begin reconstructing. Returns quickly; watch progress with ``is_completed``."""

    def stop_reconstruction(self) -> None: ...

    def is_completed(self) -> bool:
        """True once the run has finished: done, failed or cancelled."""
        ...

    def get_progress(self) -> float: ...

    def get_eta(self) -> float:
        """Seconds left, or 0 or less when there is no estimate."""
        ...

    def get_state(self) -> State: ...

    def get_error(self) -> str | None: ...

    def get_bounding_box(self, scale_factor: float) -> BoundingBox: ...
