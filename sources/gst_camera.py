"""The ROV camera: RTP/H264 over UDP, decoded and re-encoded as JPEG by GStreamer.

Stream "camera". Needs GStreamer on the PATH (``brew install gstreamer``). The bot sends to
UDP port 5600 (``ROVKIT_CAMERA_PORT`` to change). To try it without the bot, run
``just camera-sim`` in another terminal.
"""

import os

from rovkit.gstreamer import GstSource


class GstCamera(GstSource):
    stream = "camera"

    port = int(os.environ.get("ROVKIT_CAMERA_PORT", "5600"))
    jitter_ms = 20  # RTP reordering buffer: lower is quicker, higher survives lossy links
    quality = 85  # JPEG quality

    def pipeline(self) -> str:
        return (
            f"udpsrc port={self.port} "
            "caps=application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96 "
            f"! rtpjitterbuffer latency={self.jitter_ms} "
            "! rtph264depay ! h264parse ! avdec_h264 ! videoconvert "
            f"! jpegenc quality={self.quality} "
            "! queue leaky=downstream max-size-buffers=1 ! fdsink fd=1 sync=false"
        )
