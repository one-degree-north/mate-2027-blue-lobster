"""Async hub client used by modules (and tests). Reconnects automatically."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import inspect
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Annotated, Any, Self

import aiohttp
from pydantic import Field, TypeAdapter, ValidationError

from .protocol import DEFAULT_HOST, DEFAULT_PORT, WS_PATH, Error, Message, Publish, Subscribe

log = logging.getLogger(__name__)

DEFAULT_URL = f"ws://{DEFAULT_HOST}:{DEFAULT_PORT}{WS_PATH}"

Handler = Callable[[Message], Awaitable[None] | None]

_server_frame = TypeAdapter(Annotated[Message | Error, Field(discriminator="type")])


class HubClient:
    """Connects to the hub and keeps the connection alive.

    Retained publishes are remembered and replayed after a reconnect, and
    subscriptions are re-established, so a module survives a hub restart.
    Handlers run on the receive loop: keep them quick and never block.
    """

    def __init__(self, url: str = DEFAULT_URL, *, max_backoff: float = 5.0) -> None:
        self._url = url
        self._max_backoff = max_backoff
        self._handlers: dict[str, list[Handler]] = {}
        self._retained: dict[str, Publish] = {}
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle ---------------------------------------------------------------

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Connect and wait for the first connection (use ``asyncio.timeout`` to bound it)."""
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._run(), name="hub-client")
        await self._connected.wait()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def url(self) -> str:
        return self._url

    @property
    def session(self) -> aiohttp.ClientSession:
        """The shared HTTP session (valid between ``start`` and ``close``)."""
        if self._session is None:
            raise RuntimeError("HubClient is not started")
        return self._session

    # -- api ---------------------------------------------------------------------

    async def publish(self, topic: str, data: Any = None, *, retain: bool = False) -> None:
        """Send a message. Non-retained messages are dropped while disconnected."""
        frame = Publish(topic=topic, data=data, retain=retain)
        if retain:
            if data is None:
                self._retained.pop(topic, None)
            else:
                self._retained[topic] = frame
        await self._send(frame.model_dump_json())

    async def subscribe(self, pattern: str, handler: Handler) -> None:
        """Call ``handler`` for every message whose topic matches the glob ``pattern``."""
        self._handlers.setdefault(pattern, []).append(handler)
        await self._send(Subscribe(patterns=[pattern]).model_dump_json())

    # -- internals ---------------------------------------------------------------

    async def _send(self, payload: str) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send_str(payload)
        except ConnectionError:
            log.debug("send failed; connection is down")

    async def _run(self) -> None:
        assert self._session is not None
        delay = 0.25
        while True:
            try:
                async with self._session.ws_connect(self._url, heartbeat=15) as ws:
                    await self._resync(ws)
                    self._ws = ws
                    self._connected.set()
                    delay = 0.25
                    log.info("connected to hub at %s", self._url)
                    async for raw in ws:
                        if raw.type == aiohttp.WSMsgType.TEXT:
                            await self._dispatch(raw.data)
            except (aiohttp.ClientError, OSError, TimeoutError) as exc:
                log.warning("hub connection failed (%s); retrying in %.1fs", exc, delay)
            finally:
                self._ws = None
                self._connected.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_backoff)

    async def _resync(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        if self._handlers:
            await ws.send_str(Subscribe(patterns=list(self._handlers)).model_dump_json())
        for frame in self._retained.values():
            await ws.send_str(frame.model_dump_json())

    async def _dispatch(self, raw: str) -> None:
        try:
            frame = _server_frame.validate_json(raw)
        except ValidationError:
            log.warning("ignoring malformed frame from hub: %.200s", raw)
            return
        if isinstance(frame, Error):
            log.warning("hub rejected a frame: %s", frame.detail)
            return
        for pattern, handlers in list(self._handlers.items()):
            if not fnmatch.fnmatchcase(frame.topic, pattern):
                continue
            for handler in handlers:
                try:
                    result = handler(frame)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    log.exception("handler for %r failed on %s", pattern, frame.topic)
