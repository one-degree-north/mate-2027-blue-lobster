import argparse
import ctypes
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Literal

import cv2

DYLIB_PATH = os.path.join(os.path.dirname(__file__), "libPgm.dylib")


@dataclass
class BoundingBox:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float
    width: float = field(init=False)
    height: float = field(init=False)
    depth: float = field(init=False)

    def __post_init__(self):
        self.width = self.max_x - self.min_x
        self.height = self.max_y - self.min_y
        self.depth = self.max_z - self.min_z


class _PgmModule:
    def __init__(self, path: str) -> None:
        self.lib = ctypes.cdll.LoadLibrary(path)

        self.lib.run_photogrammetry_session.argtypes = [
            ctypes.c_char_p,  # imagesPath
            ctypes.c_char_p,  # outputPath
            ctypes.c_char_p,  # tempDir
            ctypes.c_int32,  # detailLevel
        ]
        self.lib.run_photogrammetry_session.restype = None

        self.lib.is_completed.argtypes = []
        self.lib.is_completed.restype = ctypes.c_bool

        self.lib.get_progress.argtypes = []
        self.lib.get_progress.restype = ctypes.c_double

        self.lib.get_eta.argtypes = []
        self.lib.get_eta.restype = ctypes.c_double

        self.lib.stop_photogrammetry_session.argtypes = []
        self.lib.stop_photogrammetry_session.restype = None

        self.lib.get_bounding_box.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.lib.get_bounding_box.restype = None

        self.lib.set_debug.argtypes = [ctypes.c_bool]
        self.lib.set_debug.restype = None

    def start(self, path: bytes, output_path: bytes, temp_dir: bytes, detail: int = 2) -> None:
        return self.lib.run_photogrammetry_session(path, output_path, temp_dir, detail)

    def stop(self) -> None:
        return self.lib.stop_photogrammetry_session()

    def is_completed(self) -> bool:
        return self.lib.is_completed()

    def get_progress(self) -> float:
        return self.lib.get_progress()

    def get_eta(self) -> float:
        return self.lib.get_eta()

    def get_bounding_box(self, path: bytes) -> BoundingBox:
        min_x, min_y, min_z = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
        max_x, max_y, max_z = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
        self.lib.get_bounding_box(
            path,
            ctypes.byref(min_x),
            ctypes.byref(min_y),
            ctypes.byref(min_z),
            ctypes.byref(max_x),
            ctypes.byref(max_y),
            ctypes.byref(max_z),
        )
        return BoundingBox(
            min_x=min_x.value,
            min_y=min_y.value,
            min_z=min_z.value,
            max_x=max_x.value,
            max_y=max_y.value,
            max_z=max_z.value,
        )

    def set_debug(self, enabled: bool) -> None:
        self.lib.set_debug(enabled)


class Photogrammetry:
    """Unified API for photogrammetry with two modes:

    1. File mode: pass cv2.VideoCapture, call .process()
    2. Streaming mode: call .receive_frame() manually
    """

    def __init__(
        self,
        *,
        video_fps: int,
        target_fps: int,
        detail: Literal[0, 1, 2, 3, 4],  # 0, 1, 2, 3, 4
        output_size: tuple[int, int] = "auto",
        temp_dir: str = "pgm-temp/",
        output_path: str = "output.usdz",
        video_capture: cv2.VideoCapture = None,
    ) -> None:
        self.lib = _PgmModule(path=DYLIB_PATH)
        self.video_capture = video_capture
        self.video_fps = video_fps
        self.target_fps = target_fps

        self.target_size = output_size
        self.detail = detail

        self.temp_dir = temp_dir
        self.output_path = output_path
        self.frames_dir = os.path.join(self.temp_dir, "reconstruction")
        self._frame_count = 0
        self._saved_frame_count = 0
        self._is_recording = False

    def _reset_dir(self, path: str) -> None:
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(path, exist_ok=True)

    def _extract_frames(self) -> None:
        self.start_recording()
        while True:
            success, frame = self.video_capture.read()
            if not success:
                break
            self.receive_frame(frame)
        self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.stop_recording()

    def process(self) -> None:
        """Extract all frames from video_capture and start reconstruction."""
        assert self.video_capture is not None, (
            "process() requires a cv2.VideoCapture — pass video_capture= to the constructor"
        )
        self._extract_frames()
        self.start_reconstruction()

    def start_recording(self) -> None:
        assert self.video_capture is None, (
            "start_recording() / receive_frame() are for streaming mode — "
            "do not pass video_capture= to the constructor when using this API"
        )
        self._reset_dir(self.frames_dir)
        self._frame_count = 0
        self._saved_frame_count = 0
        self._is_recording = True

    def receive_frame(self, frame: cv2.typing.MatLike) -> None:
        assert self.video_capture is None, (
            "receive_frame() is for streaming mode — do not pass video_capture= to the constructor when using this API"
        )
        if not self._is_recording:
            return
        if self.target_size == "auto":
            self.target_size = (frame.shape[1], frame.shape[0])
        skip_interval = max(1, round(self.video_fps / self.target_fps))
        if self._frame_count % skip_interval == 0:
            frame = cv2.resize(frame, self.target_size)
            self._saved_frame_count += 1
            cv2.imwrite(os.path.join(self.frames_dir, f"frame_{self._saved_frame_count}.png"), frame)
        self._frame_count += 1

    def stop_recording(self) -> None:
        self._is_recording = False
        # if self._saved_frame_count == 0:
        #    print("Warning: no frames were recorded.")

    def set_capture_rate(self, fps: float) -> None:
        """Set the target capture rate (frames per second). Only works between recording sessions."""
        if self._is_recording:
            raise RuntimeError("Cannot change capture rate while recording. Call stop_recording() first.")
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.target_fps = fps

    def start_reconstruction(self) -> None:
        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        self.lib.start(self.frames_dir.encode(), self.output_path.encode(), self.temp_dir.encode(), self.detail)

    def stop_reconstruction(self) -> None:
        self.lib.stop()

    def get_progress(self) -> float:
        return self.lib.get_progress()

    def get_eta(self) -> float:
        return self.lib.get_eta()

    def is_completed(self) -> bool:
        return self.lib.is_completed()

    def get_bounding_box(self, scale_factor: float) -> BoundingBox:
        # TODO: enforce condition that output path exists
        assert os.path.exists(self.output_path)
        raw = self.lib.get_bounding_box(self.output_path.encode())
        return BoundingBox(
            min_x=raw.min_x * scale_factor,
            min_y=raw.min_y * scale_factor,
            min_z=raw.min_z * scale_factor,
            max_x=raw.max_x * scale_factor,
            max_y=raw.max_y * scale_factor,
            max_z=raw.max_z * scale_factor,
        )

    def set_debug(self, enabled: bool) -> None:
        self.lib.set_debug(enabled)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Photogrammetry reconstruction from video or image sequence")
    parser.add_argument(
        "input_path",
        nargs="?",
        default=None,
        help="Path to input video file (required unless --skip-cv is used)",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_path",
        default="output.usdz",
        help="Path to output reconstruction file (default: output.usdz)",
    )
    parser.add_argument(
        "--skip-cv",
        action="store_true",
        help="Skip video processing and only run reconstruction",
    )
    parser.add_argument(
        "--skip-r",
        action="store_true",
        help="Skip reconstruction",
    )
    parser.add_argument(
        "--detail",
        type=int,
        default=4,
        choices=[0, 1, 2, 3, 4],
        help="Detail level for reconstruction (default: 4)",
    )
    parser.add_argument(
        "--video-fps",
        type=int,
        default=30,
        help="Input video frames per second (default: 30)",
    )
    parser.add_argument(
        "--target-fps",
        type=int,
        default=30,
        help="Target output frames per second for sampling (default: 30)",
    )

    args = parser.parse_args()

    # Validate input_path requirement
    if not args.skip_cv and args.input_path is None:
        parser.error("input_path is required unless --skip-cv is used")

    photogrammetry = Photogrammetry(
        video_fps=args.video_fps,
        target_fps=args.target_fps,
        detail=args.detail,
        output_path=args.output_path,
    )

    if not args.skip_cv:
        photogrammetry.start_recording()
        cap = cv2.VideoCapture(args.input_path)
        while True:
            success, frame = cap.read()
            if not success:
                break
            photogrammetry.receive_frame(frame)
        cap.release()
        photogrammetry.stop_recording()

    if not args.skip_r:
        photogrammetry.start_reconstruction()

        print("Reconstruction started...")

        while True:
            time.sleep(0.1)
            progress = photogrammetry.get_progress()
            eta = photogrammetry.get_eta()
            print(f"Progress: {progress * 100:.1f}%  ETA: {eta:.1f}s", end="\r", flush=True)

            if photogrammetry.is_completed():
                break

        print("Reconstruction complete!")

    print("Getting bounding box")
    bbox = photogrammetry.get_bounding_box(1)
    print(bbox.width, bbox.height, bbox.depth)
