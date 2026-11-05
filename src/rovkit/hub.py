"""Pub/sub hub: relays messages between modules and the browser UI over WebSockets."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web
from pydantic import ValidationError

from .protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    WS_PATH,
    Error,
    Message,
    Publish,
    Subscribe,
    Unsubscribe,
    client_frame_adapter,
)
from .video_relay import VideoRelay

log = logging.getLogger(__name__)

# Frames queued per client before the oldest are dropped. Keeps one stalled
# client (e.g. a backgrounded browser tab) from growing hub memory without bound.
CLIENT_QUEUE_SIZE = 1024

DEV_UI_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


class _Client:
    def __init__(self, name: str) -> None:
        self.name = name
        self.patterns: set[str] = set()
        self.queue: asyncio.Queue[str] = asyncio.Queue(CLIENT_QUEUE_SIZE)
        self._dropped = 0

    def wants(self, topic: str) -> bool:
        return any(fnmatch.fnmatchcase(topic, p) for p in self.patterns)

    def enqueue(self, frame: str) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                log.warning("client %s is slow; dropped %d frames", self.name, self._dropped)
        self.queue.put_nowait(frame)


class Hub:
    """Routes :class:`Publish` frames to subscribers and remembers retained values."""

    def __init__(self) -> None:
        self._clients: set[_Client] = set()
        self._sockets: set[web.WebSocketResponse] = set()
        self._retained: dict[str, Message] = {}

    # -- routing -----------------------------------------------------------------

    def publish(self, frame: Publish) -> None:
        message = Message(topic=frame.topic, ts=time.time(), data=frame.data)
        if frame.retain:
            # A retained ``null`` clears the topic, as in MQTT.
            if frame.data is None:
                self._retained.pop(frame.topic, None)
            else:
                self._retained[frame.topic] = message
        encoded = message.model_dump_json()
        for client in self._clients:
            if client.wants(frame.topic):
                client.enqueue(encoded)

    def subscribe(self, client: _Client, patterns: Iterable[str]) -> None:
        new = set(patterns) - client.patterns
        previous = set(client.patterns)
        client.patterns |= new
        # Replay retained values the client has not already been matched against.
        for topic, message in self._retained.items():
            matches_new = any(fnmatch.fnmatchcase(topic, p) for p in new)
            matched_before = any(fnmatch.fnmatchcase(topic, p) for p in previous)
            if matches_new and not matched_before:
                client.enqueue(message.model_dump_json())

    # -- connections -------------------------------------------------------------

    async def serve_client(self, ws: web.WebSocketResponse, name: str) -> None:
        client = _Client(name)
        self._clients.add(client)
        self._sockets.add(ws)
        sender = asyncio.create_task(self._send_loop(ws, client))
        log.info("client connected: %s", name)
        try:
            async for raw in ws:
                if raw.type == WSMsgType.TEXT:
                    self._handle_frame(client, raw.data)
                elif raw.type == WSMsgType.ERROR:
                    log.warning("websocket error from %s: %s", name, ws.exception())
        finally:
            self._clients.discard(client)
            self._sockets.discard(ws)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            log.info("client disconnected: %s", name)

    def _handle_frame(self, client: _Client, raw: str) -> None:
        try:
            frame = client_frame_adapter.validate_json(raw)
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            client.enqueue(Error(detail=detail).model_dump_json())
            return
        match frame:
            case Publish():
                self.publish(frame)
            case Subscribe():
                self.subscribe(client, frame.patterns)
            case Unsubscribe():
                client.patterns -= set(frame.patterns)

    async def close_all(self) -> None:
        """Close every open connection so the server can shut down promptly."""
        for ws in list(self._sockets):
            await ws.close()

    @staticmethod
    async def _send_loop(ws: web.WebSocketResponse, client: _Client) -> None:
        while True:
            await ws.send_str(await client.queue.get())


# -- aiohttp wiring --------------------------------------------------------------

HUB_KEY = web.AppKey("hub", Hub)
ORIGINS_KEY = web.AppKey("allowed_origins", frozenset[str])


def _origin_allowed(request: web.Request, allowed: frozenset[str]) -> bool:
    """Reject cross-site browser connections (any web page can open ws://localhost).

    Non-browser clients send no ``Origin`` and are always accepted.
    """
    origin = request.headers.get("Origin")
    if origin is None or origin in allowed:
        return True
    return urlsplit(origin).netloc == request.host  # same-origin (UI served by the hub)


def _require_origin(request: web.Request) -> None:
    if not _origin_allowed(request, request.app[ORIGINS_KEY]):
        log.warning("rejected websocket from origin %s", request.headers.get("Origin"))
        raise web.HTTPForbidden(reason="origin not allowed")


async def _ws_handler(request: web.Request) -> web.StreamResponse:
    _require_origin(request)
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    await request.app[HUB_KEY].serve_client(ws, f"{request.remote}")
    return ws


async def _close_clients(app: web.Application) -> None:
    await app[HUB_KEY].close_all()


def create_app(
    allowed_origins: Iterable[str] = DEV_UI_ORIGINS, ui_dir: Path | None = None
) -> web.Application:
    """Build the hub app. With ``ui_dir`` (a built ``ui/dist``), also serve the UI at ``/``."""
    app = web.Application()
    app[HUB_KEY] = Hub()
    app[ORIGINS_KEY] = frozenset(allowed_origins)
    video = VideoRelay()

    async def ingest(request: web.Request) -> web.StreamResponse:
        _require_origin(request)
        return await video.ingest(request)

    async def frames(request: web.Request) -> web.StreamResponse:
        _require_origin(request)
        return await video.frames(request)

    app.router.add_get(WS_PATH, _ws_handler)
    app.router.add_get("/video/{name}/ingest", ingest)
    app.router.add_get("/video/{name}/frames", frames)
    app.router.add_get("/video/{name}/stream.mjpg", video.view)
    if ui_dir is not None:
        index = ui_dir / "index.html"

        async def serve_index(_: web.Request) -> web.StreamResponse:
            return web.FileResponse(index)

        app.router.add_get("/", serve_index)
        app.router.add_static("/", ui_dir)
    app.on_shutdown.append(_close_clients)
    return app


def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allowed_origins: Iterable[str] = DEV_UI_ORIGINS,
    ui_dir: Path | None = None,
) -> None:
    web.run_app(
        create_app(allowed_origins, ui_dir),
        host=host,
        port=port,
        print=None,
        shutdown_timeout=2,
        access_log=None,
    )
    log.info("hub stopped")
