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

With the real camera (GStreamer comes with `./setup-deps.sh`): `just dev --source gst_camera`. To test
it without the bot, run `just camera-sim` in another terminal (color bars; `just camera-sim webcam` or `ball` for other feeds).

The photogrammetry module builds the model with RealityKit on macOS (it needs its Swift library
once: `just setup` or `just build-pgm`) and with COLMAP and OpenMVS on Linux with an NVIDIA GPU
(installed by `./setup-deps.sh`). The result is `output.usdz` on macOS and a textured
`output.glb` on Linux. COLMAP's log of the last run is `pgm-temp/colmap.log`.

Photogrammetry takes one photo per `1 / rate` seconds (the sharpest frame of each interval),
so the rate does not depend on the camera's fps. To get real sizes, scan an object of known
length, type that length into *Reference length*, and press *Use Model as Reference*; the
scale factor is then set for every later scan.

## Setup

macOS, or Ubuntu (including WSL 2). From a fresh clone:

```sh
./setup-deps.sh   # system tools (safe to re-run)
just setup        # project dependencies and the photogrammetry library
```

`./setup-deps.sh` installs Homebrew if it is missing (it asks for your password on a fresh
Mac), then:

- from the `Brewfile`: `uv`, `fnm`, `pnpm`, GStreamer (tested with 1.28.x) and `just`
- Node, at the major version in `.node-version`, through fnm

It adds `eval "$(fnm env --use-on-cd)"` to `~/.zshrc` so the right Node is active in the
project; open a new terminal before `just setup`.

`just setup` installs the Python and UI packages exactly as pinned in `uv.lock` and
`ui/pnpm-lock.yaml`, then, on macOS, builds the photogrammetry library (`just build-pgm`
rebuilds it; it needs the Xcode Command Line Tools).

### Ubuntu and WSL 2

`./setup-deps.sh` uses apt and the tools' own installers instead of Homebrew, and also builds
the photogrammetry tools from source: COLMAP (with CUDA, for the GPU) and OpenMVS (textures).
That takes a long time the first time. Requirements:

- An NVIDIA GPU with CUDA 12.8 or newer (the RTX 50 series needs it). On WSL, install the
  driver on Windows only; `nvidia-smi` must work inside WSL before you run the script. The
  script installs the CUDA toolkit, never a Linux driver.
- On WSL, mirrored networking, or the bot's UDP video cannot reach the hub. In Windows put
  `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig` and run `wsl --shutdown`.
- Keep the repository in the Linux filesystem (`~/`), not under `/mnt/c`.

COLMAP and OpenMVS are pinned by `COLMAP_REF` and `OPENMVS_REF` at the top of the script.

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
