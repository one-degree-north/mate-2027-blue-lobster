"""Command-line entry point: ``rovkit hub | run | replay``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Sequence
from pathlib import Path

from aiohttp import web

from . import hub, recorder
from .protocol import DEFAULT_HOST, DEFAULT_PORT, WS_PATH
from .supervisor import ModuleSpec, Supervisor, discover

DEFAULT_UI_DIR = Path("ui/dist")


def _add_hub_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="extra browser origin allowed to connect (repeatable)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rovkit", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    hub_cmd = sub.add_parser("hub", help="run only the message hub")
    _add_hub_args(hub_cmd)
    hub_cmd.add_argument("--ui-dir", type=Path, help="serve a built UI from this directory")

    run_cmd = sub.add_parser("run", help="run the hub and every module in modules/")
    _add_hub_args(run_cmd)
    run_cmd.add_argument("--modules-dir", type=Path, default=Path("modules"))
    run_cmd.add_argument("--sources-dir", type=Path, default=Path("sources"))
    run_cmd.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME",
        help="run only this video source from the sources dir (repeatable; default: all)",
    )
    run_cmd.add_argument("--reload", action="store_true", help="restart modules when files change")
    run_cmd.add_argument(
        "--record",
        nargs="?",
        const=Path("recordings"),
        type=Path,
        metavar="DIR",
        help="record all traffic to an MCAP file in DIR (default: recordings/)",
    )
    run_cmd.add_argument(
        "--ui-dir",
        type=Path,
        default=DEFAULT_UI_DIR,
        help="built UI to serve (default: %(default)s, skipped if missing)",
    )

    replay_cmd = sub.add_parser("replay", help="publish a recording to a running hub")
    replay_cmd.add_argument("file", type=Path)
    replay_cmd.add_argument("--speed", type=float, default=1.0)
    replay_cmd.add_argument("--loop", action="store_true")
    replay_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def _hub_url(port: int) -> str:
    return f"ws://127.0.0.1:{port}{WS_PATH}"


def _install_stop_handler(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


def _select_sources(sources_dir: Path, names: list[str]) -> list[ModuleSpec]:
    """Every source in ``sources_dir``, or only the named ones (an unknown name is an error)."""
    found = discover(sources_dir, "source") if sources_dir.is_dir() else []
    if not names:
        return found
    unknown = sorted(set(names) - {s.id for s in found})
    if unknown:
        available = ", ".join(s.id for s in found) or "none"
        raise SystemExit(f"rovkit: unknown source {', '.join(unknown)} (available: {available})")
    return [s for s in found if s.id in names]


async def _run_all(args: argparse.Namespace) -> None:
    log = logging.getLogger("rovkit")
    specs = discover(args.modules_dir) if args.modules_dir.is_dir() else []
    if not specs:
        log.warning("no modules found in %s/", args.modules_dir)
    specs += _select_sources(args.sources_dir, args.source)
    ui_dir = args.ui_dir if (args.ui_dir / "index.html").exists() else None
    origins = [*hub.DEV_UI_ORIGINS, *args.allow_origin]

    runner = web.AppRunner(hub.create_app(origins, ui_dir), shutdown_timeout=2, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, args.host, args.port).start()
    log.info("hub on ws://%s:%d%s (%d processes)", args.host, args.port, WS_PATH, len(specs))
    if ui_dir is not None:
        log.info("UI at http://%s:%d/", args.host, args.port)

    stop = asyncio.Event()
    _install_stop_handler(stop)
    url = _hub_url(args.port)
    supervisor = Supervisor(specs, url, reload=args.reload)
    tasks = [asyncio.create_task(supervisor.run(), name="supervisor")]
    recording_stop = asyncio.Event()
    if args.record is not None:
        path = recorder.new_recording_path(args.record)
        tasks.append(asyncio.create_task(recorder.record(url, path, recording_stop)))
    try:
        await stop.wait()
    finally:
        supervisor.stop()
        await tasks[0]  # modules go down first so their final status is recorded
        recording_stop.set()
        await asyncio.gather(*tasks[1:])
        await runner.cleanup()


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    origins = [*hub.DEV_UI_ORIGINS, *getattr(args, "allow_origin", [])]
    if args.command == "hub":
        hub.run(args.host, args.port, origins, args.ui_dir)
    elif args.command == "run":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run_all(args))
    elif args.command == "replay":
        asyncio.run(
            recorder.replay(_hub_url(args.port), args.file, speed=args.speed, loop=args.loop)
        )
