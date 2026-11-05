"""Plays a video file in a loop, as if it were the camera. For testing without the bot.

Stream "file". Set ``ROVKIT_VIDEO_FILE`` to the clip to play; until you do, the source is idle.
The path may not contain spaces.
"""

import os
from pathlib import Path

from rovkit.gstreamer import GstSource


class VideoFile(GstSource):
    stream = "file"

    path = os.environ.get("ROVKIT_VIDEO_FILE", "")
    quality = 85

    def idle_reason(self) -> str | None:
        if not self.path:
            return "no clip chosen: set ROVKIT_VIDEO_FILE=path/to/clip.mp4"
        return None

    def problem(self) -> str | None:
        if any(char.isspace() for char in self.path):
            return f"video path must not contain spaces: {self.path!r}"
        if not Path(self.path).is_file():
            return f"video file not found: {self.path} (set ROVKIT_VIDEO_FILE)"
        return None

    def pipeline(self) -> str:
        # fdsink keeps its clock here (no sync=false), so the clip plays at its own speed.
        return (
            f"filesrc location={self.path} ! decodebin ! videoconvert "
            f"! jpegenc quality={self.quality} "
            "! queue leaky=downstream max-size-buffers=1 ! fdsink fd=1"
        )
