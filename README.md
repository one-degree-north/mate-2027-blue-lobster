# MATE ROV control UI

Python modules talk to a browser UI through a local message hub.

```
browser (ui/)  <--WebSocket-->  hub  <--WebSocket-->  modules/*.py  (one process each)
       ^                         |  ^
       └── MJPEG video <── hub <─┘  └── JPEG frames from sources/*.py and from modules,
                                         and frames back out to the modules that ask
```

- `src/rovkit/` – the framework: `Module` and `Source` base classes, widgets, hub, loader, recorder
- `modules/` – team code, one file per module (see [docs/writing-modules.md](docs/writing-modules.md))
- `sources/` – where video enters: the GStreamer camera, a video-file player, a test pattern
- `ui/` – Vite + Svelte + Dockview web UI (video in the centre, auto-built panel per module)

## Video

A **source** produces frames; **modules** read them with `self.frames()` and may publish an
annotated stream of their own. The video panel's dropdown lists every stream.

| Source | Stream | Needs |
|---|---|---|
| `gst_camera` | `camera` | GStreamer; the bot sends RTP/H264 to UDP 5600 |
| `video_file` | `file` | GStreamer; `ROVKIT_VIDEO_FILE=path/to/clip.mp4` |
| `pattern_camera` | `pattern` | nothing |

Modules read the stream named by `ROVKIT_SOURCE` (default `camera`). Run one source with
`--source`, e.g. no hardware at all:

```sh
ROVKIT_SOURCE=pattern just dev --source pattern_camera
```

With the real camera: `brew install gstreamer`, then `just dev --source gst_camera`. To test
it without the bot, run `just camera-sim` in another terminal (color bars; `just camera-sim webcam` or `ball` for other feeds).

The photogrammetry module needs its Swift library once: `just build-pgm` (macOS only).

## Setup

```sh
uv sync                 # Python deps
cd ui && pnpm install   # UI deps
```

## Run

Development (UI hot reload; modules restart when saved):

```sh
uv run rovkit run --reload       # hub + every module in modules/
cd ui && pnpm dev                # http://localhost:5173
```

Single process, no Node needed at the pool (build the UI once):

```sh
cd ui && pnpm build
uv run rovkit run                # http://127.0.0.1:8765
```

Useful flags: `--record [DIR]` writes an MCAP log, `--host 0.0.0.0` exposes the hub to the LAN
(also add `--allow-origin http://<address>:<port>` if the UI is served from elsewhere),
`--modules-dir`, `--sources-dir`, `--source NAME` (run only that source), `-v`.
`uv run rovkit replay FILE` plays a recording back into a running hub.

The hub has no authentication. By default it binds to `127.0.0.1` and rejects browser
connections from origins other than its own and the Vite dev server.

## Checks

```sh
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
cd ui && pnpm check
```

## Protocol

Every frame is JSON with a `type`. Clients send `publish` (`topic`, `data`, `retain`),
`subscribe` and `unsubscribe` (`patterns`, `fnmatch` globs). The hub sends `message`
(`topic`, `ts`, `data`) and `error`. A retained value is replayed to later subscribers;
retaining `null` clears it. Topic conventions are listed in `src/rovkit/protocol.py`.

Video does not travel on the bus: sources and modules push JPEGs to `/video/<name>/ingest`,
the UI shows `/video/<name>/stream.mjpg`, and modules receive the newest frames (older ones
are skipped) from `/video/<name>/frames` as binary WebSocket messages.
