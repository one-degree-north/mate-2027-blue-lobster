"""Discovers modules and runs each in its own supervised process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .client import HubClient
from .protocol import MODULE_ID_PATTERN, MODULE_INDEX_TOPIC, status_topic
from .runner import HUB_URL_ENV, RELOAD_ENV

log = logging.getLogger(__name__)

MIN_BACKOFF = 1.0
MAX_BACKOFF = 10.0
STABLE_AFTER = 30.0  # a run this long resets the restart backoff
STOP_TIMEOUT = 5.0
TAIL_LINES = 10
RELOAD_POLL = 1.0


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    path: Path
    kind: Literal["module", "source"] = "module"


def discover(modules_dir: Path, kind: Literal["module", "source"] = "module") -> list[ModuleSpec]:
    """Each ``*.py`` file or package directory is one module; ``_``/``.`` prefixes are skipped."""
    specs: list[ModuleSpec] = []
    for entry in sorted(modules_dir.iterdir()):
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_file() and entry.suffix == ".py":
            module_id = entry.stem
        elif entry.is_dir() and (entry / "__init__.py").exists():
            module_id = entry.name
        else:
            continue
        if not re.match(MODULE_ID_PATTERN, module_id):
            log.warning("skipping %s: module names must match %s", entry, MODULE_ID_PATTERN)
            continue
        specs.append(ModuleSpec(module_id, entry, kind))
    return specs


def _source_mtime(path: Path) -> float:
    files = path.rglob("*.py") if path.is_dir() else [path]
    return max((f.stat().st_mtime for f in files), default=0.0)


@dataclass
class _State:
    spec: ModuleSpec
    restarts: int = 0
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    restart_requested: asyncio.Event = field(default_factory=asyncio.Event)


class Supervisor:
    def __init__(self, specs: list[ModuleSpec], hub_url: str, *, reload: bool = False) -> None:
        self._states = [_State(spec) for spec in specs]
        self._hub_url = hub_url
        self._reload = reload
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Run until :meth:`stop`. Children are terminated before this returns."""
        async with HubClient(self._hub_url) as hub, asyncio.TaskGroup() as group:
            modules = [s.spec.id for s in self._states if s.spec.kind == "module"]
            await hub.publish(MODULE_INDEX_TOPIC, modules, retain=True)
            for state in self._states:
                group.create_task(self._supervise(state, hub), name=f"supervise-{state.spec.id}")
            if self._reload:
                group.create_task(self._watch_sources())

    async def _watch_sources(self) -> None:
        seen = {s.spec.path: _source_mtime(s.spec.path) for s in self._states}
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), RELOAD_POLL)
            for state in self._states:
                try:
                    mtime = _source_mtime(state.spec.path)
                except OSError:
                    continue
                if mtime != seen[state.spec.path]:
                    seen[state.spec.path] = mtime
                    log.info("%s changed; restarting", state.spec.id)
                    state.restart_requested.set()

    async def _supervise(self, state: _State, hub: HubClient) -> None:
        spec = state.spec

        async def report(name: str, **extra: object) -> None:
            await hub.publish(
                status_topic(spec.id, spec.kind),
                {"state": name, "restarts": state.restarts, "tail": list(state.tail), **extra},
                retain=True,
            )

        backoff = MIN_BACKOFF
        while not self._stopping.is_set():
            state.restart_requested.clear()
            state.tail.clear()
            await report("starting")
            env = {**os.environ, HUB_URL_ENV: self._hub_url, "PYTHONUNBUFFERED": "1"}
            if self._reload:
                env[RELOAD_ENV] = "1"
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "rovkit.runner", str(spec.path), "--id", spec.id,
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )  # fmt: skip
            started = time.monotonic()
            await report("running", pid=proc.pid)
            output = asyncio.create_task(self._forward_output(state, proc))
            exited = asyncio.create_task(proc.wait())
            interrupt = [
                asyncio.create_task(self._stopping.wait()),
                asyncio.create_task(state.restart_requested.wait()),
            ]
            await asyncio.wait({exited, *interrupt}, return_when=asyncio.FIRST_COMPLETED)
            for task in interrupt:
                task.cancel()

            if not exited.done():
                await _terminate(proc)
            await exited
            await output

            if self._stopping.is_set():
                break
            if state.restart_requested.is_set():
                state.restarts += 1
                backoff = MIN_BACKOFF
                continue

            if time.monotonic() - started >= STABLE_AFTER:
                backoff = MIN_BACKOFF
            delay, backoff = backoff, min(backoff * 2, MAX_BACKOFF)
            state.restarts += 1
            log.error(
                "module %s exited with code %s; restarting in %.1fs",
                spec.id,
                proc.returncode,
                delay,
            )
            await report("crashed", code=proc.returncode, retryIn=delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wait_for_interrupt(state), delay)
        await report("stopped")

    async def _wait_for_interrupt(self, state: _State) -> None:
        stop = asyncio.create_task(self._stopping.wait())
        restart = asyncio.create_task(state.restart_requested.wait())
        try:
            await asyncio.wait({stop, restart}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            restart.cancel()

    @staticmethod
    async def _forward_output(state: _State, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            state.tail.append(line)
            print(f"[{state.spec.id}] {line}", flush=True)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
    except TimeoutError:
        log.warning("pid %s ignored SIGTERM; killing", proc.pid)
        proc.kill()
