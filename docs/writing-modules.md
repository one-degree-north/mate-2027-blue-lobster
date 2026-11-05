# Writing a module

A module is one Python file in `modules/`. It declares widgets (buttons, sliders, readouts, …)
and implements what they do. The UI builds a panel for it automatically; you never touch
JavaScript, sockets, or processes.

## 5-minute start

```sh
cp modules/_template.py modules/depth.py    # lowercase_with_underscores.py
uv run rovkit run --reload                  # saving the file restarts just your module
```

Open http://localhost:5173 (dev) or http://localhost:8765 (after `cd ui && pnpm build`).

```python
import asyncio
from rovkit import Module, Readout, Slider, Button


class Depth(Module):
    title = "Depth sensor"
    reading = Readout("Depth", unit="m")
    offset = Slider(-1, 1, step=0.01, default=0, unit="m")
    zero = Button("Zero sensor")

    async def on_zero(self):  # runs when the button is clicked
        self.offset = -self.raw

    async def run(self):  # optional main loop
        while True:
            self.raw = await read_sensor()
            self.reading = self.raw + self.offset  # assigning updates the UI
            await asyncio.sleep(0.1)
```

## Rules of the road

- **One `Module` subclass per file** (or per package directory with an `__init__.py`).
  The file name is the module's id. Names starting with `_` are ignored.
- **Don't override `__init__`.** Use `on_start` for setup.
- **Never block the event loop.** `time.sleep`, camera reads, and heavy OpenCV/NumPy work
  freeze every widget in your module. Use `await asyncio.sleep(...)`, or
  `await asyncio.to_thread(blocking_function)`.
- **A crash is not the end.** If `run()` raises, the process exits and the loader restarts
  it with backoff. The UI shows `crashed` with the last lines of output.
- **Handlers run one at a time**, in order. Keep them quick; start long work as a task.

## Widgets

| Widget | UI | Read `self.x` | Assign `self.x = …` | Handler |
|---|---|---|---|---|
| `Button("label")` | button | – | – | `async def on_x(self)` |
| `Slider(min, max, step=, default=, unit=)` | slider | float | moves the slider | `async def on_x(self, value)` |
| `Number(min, max, step=, default=, unit=)` | number field | float | sets it | `async def on_x(self, value)` |
| `Select(options, default=)` | dropdown | the chosen id | picks one | `async def on_x(self, value)` |
| `Toggle(default=)` | switch | bool | flips it | `async def on_x(self, value)` |
| `Readout("label", unit=, default=)` | text | last value | updates it | – |
| `Status("label")` | coloured text | `{"text", "tone"}` | text, or `(text, tone)` | – |
| `Progress("label")` | progress bar | last fraction | sets 0–1 (`None` = unknown) | – |
| `Plot("label", unit=, window=, y_min=, y_max=)` | scrolling chart | last point | adds a point | – |

`Select` takes ids (`Select(["a", "b"])`) or a mapping of id to the label shown
(`Select({"lo": "Low", "hi": "High"})`). `Number` clamps to its bounds but does not snap to
`step`. `Status` tones are `info`, `ok`, `warn` and `error`:
`self.status = ("Reconstruction failed", "error")`.

Handlers are found by name: `on_<widget>`. A typo (`on_gian`) is an error when the file loads,
not a silent no-op. Values from the UI are validated and clamped before your code sees them.

### Switching widgets off

While a module is busy, grey out controls that must not be touched:

```python
self.disable("gain", "start")  # greyed in the UI, and their commands are ignored
self.enable("gain")
```

Only input widgets can be disabled. The module refuses the commands itself, so a stale or
hand-made UI message cannot get through. `self.update("x", value)` assigns a widget only
when the value changed, which suits a loop that runs every frame.

Widgets appear in the order you declare them. Widget state is remembered by the hub, so
refreshing the page never loses it.

## Lifecycle

`on_start()` → `run()` (optional) → `on_stop()`. Open hardware in `on_start`, release it in
`on_stop`; it runs on Ctrl-C and on restarts.

## Video

```python
from rovkit.video import encode_jpeg


async def run(self):
    stream = self.video("main")  # name it; the UI lists all streams
    while True:
        frame = await asyncio.to_thread(self.capture)  # e.g. an RGB numpy array
        stream.push_jpeg(
            encode_jpeg(frame)
        )  # OpenCV (BGR): cv2.imencode(".jpg", frame)[1].tobytes()
```

`push_jpeg` is thread-safe and keeps only the newest frame, so a slow network drops frames
instead of adding delay.

**Draw detections over the video** by publishing normalized (0–1) boxes. The UI draws them
on a canvas, so no pixels are re-encoded:

```python
self.publish(
    "video/main/overlay", {"boxes": [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.2, "label": "crab"}]}
)
```

The overlay is not frame-locked; it can trail the picture by a frame or two.

### Reading video

Frames enter through a *source* (see below). A module reads the newest ones from it:

```python
import asyncio

from rovkit import Button, Module, Readout


class Counter(Module):
    start_counting = Button("Start")
    stop_counting = Button("Stop")
    seen = Readout("Seen", default=0)

    async def on_start_counting(self):
        self.task = self.spawn(self.count(), "count")  # failures are logged, not lost

    async def on_stop_counting(self):
        self.task.cancel()

    async def count(self):
        async with self.frames() as frames:  # leaving the block closes the link
            out = self.video(self.id)  # our own annotated stream, listed in the UI
            try:
                async for jpeg in frames:  # newest frame; a busy module skips frames
                    annotated = await asyncio.to_thread(self.process, jpeg)  # decode, detect, draw
                    out.push_jpeg(annotated)
            finally:
                await out.close()  # takes the stream off the UI's list
```

- `self.frames()` reads the stream named by `ROVKIT_SOURCE` (default `camera`);
  `self.frames("pattern")` reads a specific one.
- `await frames.next(timeout=1.0)` raises `TimeoutError` if the source goes quiet.
- A module's streams are removed from the UI when it stops, even without `close()`.
- Decode with `cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)` (BGR) in a thread.

### Sources

A **source** is where video enters. It runs like a module (own process, restarted if it
crashes) but has no panel and no widgets. Put it in `sources/` (see `sources/_template.py`):

```python
class Webcam(Source):
    async def run(self):
        stream = self.video("webcam")
        self.report("live")  # shown in the UI header: "webcam: live"
        while True:
            stream.push_jpeg(await grab_jpeg())
```

For GStreamer, subclass `rovkit.gstreamer.GstSource` and return a `gst-launch-1.0` pipeline
ending in `jpegenc ! fdsink fd=1` from `pipeline()`; see `sources/gst_camera.py`. It shows a
"NO SIGNAL" picture when frames stop and restarts the pipeline if it exits.
If the source cannot start, override `idle_reason()` (nothing to do yet, shown as "idle") or
`problem()` (something is wrong, shown as an error); each is logged once, not on every check.

## Talking to other modules

Everything is a topic with a JSON value. Publish yours, subscribe to others':

```python
async def on_start(self):
    await self.subscribe("modules/depth/state/reading", self.handle_depth)


def handle_depth(self, message):  # message.topic, message.data, message.ts
    ...


self.publish("vision/crab_count", 3, retain=True)  # retain=True: late joiners get the last value
```

`on_<name>` is reserved for widget handlers, so name subscription callbacks something else
(`handle_depth`, `depth_changed`, …).

## Recording and replay

```sh
uv run rovkit run --record          # writes recordings/<timestamp>.mcap
uv run rovkit hub                   # in another terminal: a hub with no modules
uv run rovkit replay recordings/20261101-101500.mcap --speed 2
```

MCAP files also open in Foxglove and other MCAP tools.
