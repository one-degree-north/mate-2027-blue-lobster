set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install Python and UI dependencies exactly as locked, then build the photogrammetry library (macOS).
# Needs the tools from ./setup-deps.sh.
setup:
    uv sync --frozen
    pnpm --dir ui install --frozen-lockfile
    just build-pgm

# Hub + modules + sources (restart on save) and the Vite UI at http://localhost:5173; Ctrl-C stops both.
# Extra arguments go to `rovkit run`, e.g. `just dev --source pattern_camera`.
dev *args:
    #!/usr/bin/env bash
    trap 'kill 0' EXIT   # on exit, stop everything this recipe started
    uv run rovkit run --reload {{args}} &
    pnpm --dir ui dev &
    wait

# Build the UI and serve everything from the hub at http://127.0.0.1:8765
serve *args:
    pnpm --dir ui build
    uv run rovkit run {{args}}

# Build the Swift library the photogrammetry module needs on macOS
[macos]
build-pgm:
    swiftc -emit-library -o modules/photogrammetry/libPgm.dylib modules/photogrammetry/pgm.swift

# Nothing to build on Linux: photogrammetry uses COLMAP and OpenMVS, installed by ./setup-deps.sh
[linux]
build-pgm:
    @echo "nothing to build: photogrammetry uses COLMAP and OpenMVS (./setup-deps.sh)"

# Send a fake bot feed to the camera source on UDP 5600 (needs GStreamer).
# feed: bars = static colour bars (default), ball = a moving ball, webcam = your Mac's camera
camera-sim feed="bars":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{feed}}" in
      webcam) src="avfvideosrc ! video/x-raw,width=1280,height=720" ;;
      bars)   src="videotestsrc is-live=true pattern=smpte ! video/x-raw,width=1280,height=720,framerate=30/1" ;;
      ball)   src="videotestsrc is-live=true pattern=ball ! video/x-raw,width=1280,height=720,framerate=30/1" ;;
      *) echo "unknown feed '{{feed}}': use webcam, bars or ball" >&2; exit 1 ;;
    esac
    exec gst-launch-1.0 -v $src ! videoconvert ! video/x-raw,format=I420 \
      ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=5000 key-int-max=30 \
      ! rtph264pay pt=96 config-interval=1 ! udpsink host=127.0.0.1 port=5600

# Lint, type check, and test both halves
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check
    pnpm --dir ui check

test:
    uv run pytest
