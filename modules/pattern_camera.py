"""Generated video + moving detection box, for developing the UI without a camera."""

import asyncio
import math
import time

from PIL import Image, ImageDraw

from rovkit import Module, Readout, Slider, Toggle
from rovkit.video import encode_jpeg

WIDTH, HEIGHT = 640, 360
BITS = 32  # width of the timestamp strip (see timestamp_strip)


def timestamp_strip(draw: ImageDraw.ImageDraw, millis: int) -> None:
    """Draw ``millis`` (low 32 bits) as black/white blocks along the top edge.

    Lets a test script read the capture time back out of a screenshot to
    measure end-to-end latency.
    """
    block = WIDTH // BITS
    for i in range(BITS):
        on = (millis >> (BITS - 1 - i)) & 1
        colour = (255, 255, 255) if on else (0, 0, 0)
        draw.rectangle([i * block, 0, (i + 1) * block - 1, 11], fill=colour)


def render(frame: int, show_box: bool) -> tuple[bytes, dict[str, float]]:
    t = frame / 30
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(0, HEIGHT, 4):
        shade = int(40 + 60 * y / HEIGHT)
        draw.rectangle([0, y, WIDTH, y + 4], fill=(10, shade, shade + 40))
    cx = WIDTH / 2 + math.cos(t) * WIDTH * 0.3
    cy = HEIGHT / 2 + math.sin(t * 1.3) * HEIGHT * 0.25
    draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(240, 120, 60))
    draw.text((10, 20), f"frame {frame}  {time.strftime('%H:%M:%S')}", fill=(255, 255, 255))
    timestamp_strip(draw, int(time.time() * 1000))
    box = {"x": (cx - 40) / WIDTH, "y": (cy - 40) / HEIGHT, "w": 80 / WIDTH, "h": 80 / HEIGHT}
    return encode_jpeg(image, quality=75), box


class PatternCamera(Module):
    title = "Test camera"

    fps = Slider(1, 30, step=1, default=15, label="Frame rate", unit="fps")
    boxes = Toggle(default=True, label="Show detection box")
    frames = Readout("Frames sent", default=0)

    async def run(self) -> None:
        stream = self.video("main")
        frame = 0
        while True:
            started = time.monotonic()
            jpeg, box = await asyncio.to_thread(render, frame, self.boxes)
            stream.push_jpeg(jpeg)
            boxes = [{**box, "label": "target"}] if self.boxes else []
            self.publish("video/main/overlay", {"boxes": boxes})
            frame += 1
            self.frames = frame
            await asyncio.sleep(max(0.0, 1 / self.fps - (time.monotonic() - started)))
