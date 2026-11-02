# MATE ROV control UI

Python modules talk to a browser UI through a local message hub.

```
browser (ui/)  <--WebSocket-->  hub  <--WebSocket-->  modules/*.py  (one process each)
       ^                         |
       └── MJPEG video <── hub <─┘ JPEG frames from modules
```

- `src/rovkit/` – the framework: `Module` base class, widgets, hub, loader, recorder
- `modules/` – team code, one file per module (see [docs/writing-modules.md](docs/writing-modules.md))
- `ui/` – Vite + Svelte + Dockview web UI (video in the centre, auto-built panel per module)

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
`--modules-dir`, `-v`. `uv run rovkit replay FILE` plays a recording back into a running hub.

The hub has no authentication. By default it binds to `127.0.0.1` and rejects browser
connections from origins other than its own and the Vite dev server.

## Checks

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests modules && uv run pytest
cd ui && pnpm check
```

## Protocol

Every frame is JSON with a `type`. Clients send `publish` (`topic`, `data`, `retain`),
`subscribe` and `unsubscribe` (`patterns`, `fnmatch` globs). The hub sends `message`
(`topic`, `ts`, `data`) and `error`. A retained value is replayed to later subscribers;
retaining `null` clears it. Topic conventions are listed in `src/rovkit/protocol.py`.

Video does not travel on the bus: modules push JPEGs to `/video/<name>/ingest`, and the UI
shows `/video/<name>/stream.mjpg`.
