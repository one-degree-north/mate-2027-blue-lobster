"""Child-process entry point: ``python -m rovkit.runner <path> --id <module_id>``.

Imports the module file, instantiates its :class:`Module` subclass, and serves it
until SIGTERM/SIGINT. The loader (``rovkit run``) starts one of these per module.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import logging
import os
import signal
import sys
from pathlib import Path
from types import ModuleType

from .client import DEFAULT_URL, HubClient
from .module import Module

HUB_URL_ENV = "ROVKIT_HUB_URL"
RELOAD_ENV = "ROVKIT_RELOAD"
_NAMESPACE = "rovkit_user_modules"

log = logging.getLogger("runner")


def load_module_class(path: Path) -> type[Module]:
    """Import ``path`` (a ``.py`` file or a package directory) and find its one Module subclass."""
    entry = path / "__init__.py" if path.is_dir() else path
    name = f"{_NAMESPACE}.{path.stem}"
    spec = importlib.util.spec_from_file_location(
        name, entry, submodule_search_locations=[str(path)] if path.is_dir() else None
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    if _NAMESPACE not in sys.modules:
        sys.modules[_NAMESPACE] = ModuleType(_NAMESPACE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    found = [
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, Module) and cls is not Module and cls.__module__ == name
    ]
    if len(found) != 1:
        names = [c.__name__ for c in found]
        raise ImportError(f"{path} must define exactly one Module subclass, found {names}")
    return found[0]


async def serve(path: Path, module_id: str, hub_url: str) -> None:
    cls = load_module_class(path)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows: fall back to KeyboardInterrupt
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    async with HubClient(hub_url) as client:
        log.info("module %s (%s) running", module_id, cls.__name__)
        await cls(module_id, client)._serve(stop)


def main() -> None:
    if os.environ.get(RELOAD_ENV):
        # Same-size edits within one second would otherwise reuse a stale .pyc.
        sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(prog="python -m rovkit.runner")
    parser.add_argument("path", type=Path)
    parser.add_argument("--id", required=True, dest="module_id")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stdout
    )
    try:
        asyncio.run(
            serve(args.path.resolve(), args.module_id, os.environ.get(HUB_URL_ENV, DEFAULT_URL))
        )
    except Exception:
        log.exception("module crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
