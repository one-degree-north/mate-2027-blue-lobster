"""Base class for ROV modules. Subclass it, declare widgets, and implement behavior.

    class Depth(Module):
        title = "Depth sensor"
        reading = Readout("Depth", unit="m")

        async def run(self):
            while True:
                self.reading = await read_sensor()
                await asyncio.sleep(0.1)

Drop the file in ``modules/`` and run ``rovkit run``. Each module is its own process.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from .client import Handler, HubClient
from .protocol import (
    Message,
    cmd_topic,
    schema_topic,
    state_topic,
)
from .video import STREAM_NAME_PATTERN, VideoStream, ingest_url
from .widgets import Button, Widget

_LIFECYCLE = frozenset({"on_start", "on_stop"})
_DRAIN_TIMEOUT = 1.0


class Module:
    """One module = one subclass of this, one process, one panel in the UI."""

    title: ClassVar[str | None] = None
    """Panel title; defaults to the class name."""

    _widgets: ClassVar[dict[str, Widget[Any]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        widgets: dict[str, Widget[Any]] = {}
        for base in reversed(cls.__mro__):
            widgets |= {n: v for n, v in vars(base).items() if isinstance(v, Widget)}
        cls._widgets = widgets
        for name, member in vars(cls).items():
            if name.startswith("on_") and name not in _LIFECYCLE and callable(member):
                cls._check_handler(name, member, widgets)

    @staticmethod
    def _check_handler(
        name: str, handler: Callable[..., Any], widgets: dict[str, Widget[Any]]
    ) -> None:
        widget = widgets.get(name.removeprefix("on_"))
        if widget is None:
            raise TypeError(
                f"{name}() looks like a widget handler but there is no widget "
                f"{name.removeprefix('on_')!r} (widgets: {list(widgets)}). "
                "Rename the method if it is not a handler; "
                "on_start and on_stop are the only other on_* names."
            )
        if not widget.accepts_input:
            raise TypeError(f"{name}(): {widget.kind} {widget.name!r} is output-only")
        # `self` plus the new value for sliders and toggles; just `self` for buttons.
        expected = 1 if isinstance(widget, Button) else 2
        if len(inspect.signature(handler).parameters) != expected:
            args = "self" if expected == 1 else "self, value"
            raise TypeError(f"{name}() must take ({args}) to handle a {widget.kind}")

    # -- construction (done by the runner; subclasses use on_start instead of __init__) --

    def __init__(self, module_id: str, client: HubClient) -> None:
        self.id = module_id
        self.log = logging.getLogger(f"module.{module_id}")
        self._client = client
        self._widget_values: dict[str, Any] = {n: w.initial() for n, w in self._widgets.items()}
        self._loop = asyncio.get_running_loop()
        self._thread_id = threading.get_ident()
        self._outbox: asyncio.Queue[tuple[str, Any, bool]] = asyncio.Queue()
        self._commands: asyncio.Queue[Callable[[], Awaitable[None] | None]] = asyncio.Queue()
        self._video: list[asyncio.Task[None]] = []

    # -- override these --------------------------------------------------------------

    async def on_start(self) -> None:
        """Called once the hub is connected and widgets are visible. Set things up here."""

    async def run(self) -> None:
        """Optional main loop. If it raises, the process exits and is restarted."""

    async def on_stop(self) -> None:
        """Called on shutdown. Release hardware here."""

    # -- helpers for module authors ----------------------------------------------------

    def publish(self, topic: str, data: Any = None, *, retain: bool = False) -> None:
        """Publish to any topic. Safe to call from any thread."""
        if threading.get_ident() == self._thread_id:
            self._outbox.put_nowait((topic, data, retain))
        else:
            self._loop.call_soon_threadsafe(self._outbox.put_nowait, (topic, data, retain))

    async def subscribe(self, pattern: str, handler: Handler) -> None:
        """Receive messages on other topics, e.g. another module's state."""
        await self._client.subscribe(pattern, handler)

    def video(self, name: str = "main") -> VideoStream:
        """Open an outgoing video stream; push JPEG frames with ``stream.push_jpeg``."""
        if not STREAM_NAME_PATTERN.match(name):
            raise ValueError(f"video name {name!r} must match {STREAM_NAME_PATTERN.pattern}")
        stream = VideoStream(
            name, ingest_url(self._client.url, name), self._client.session, self._loop
        )
        self._video.append(stream.start())
        self.publish(f"video/{name}/meta", {"module": self.id}, retain=True)
        return stream

    # -- internals ---------------------------------------------------------------------

    def _set_widget(self, widget: Widget[Any], value: Any) -> None:
        self._widget_values[widget.name] = value
        self.publish(state_topic(self.id, widget.name), value, retain=True)

    def _schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title or type(self).__name__,
            "widgets": [w.schema() for w in self._widgets.values()],
        }

    async def _pump(self) -> None:
        """Single sender task, so publishes from different places stay ordered."""
        while True:
            topic, data, retain = await self._outbox.get()
            try:
                await self._client.publish(topic, data, retain=retain)
            except Exception:
                self.log.exception("could not publish to %s", topic)
            finally:
                self._outbox.task_done()

    async def _command_worker(self) -> None:
        """Runs UI handlers one at a time, in order, so they never overlap."""
        while True:
            call = await self._commands.get()
            try:
                result = call()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self.log.exception("widget handler failed")

    def _on_command(self, message: Message) -> None:
        name = message.topic.rsplit("/", 1)[-1]
        widget = self._widgets.get(name)
        if widget is None or not widget.accepts_input:
            self.log.warning("ignoring command for unknown or output-only widget %r", name)
            return
        handler = getattr(self, f"on_{name}", None)
        if isinstance(widget, Button):
            if handler is not None:
                self._commands.put_nowait(handler)
            return
        try:
            value = widget.coerce(message.data)  # the UI is untrusted input
        except ValueError as exc:
            self.log.warning("rejected command: %s", exc)
            return
        self._set_widget(widget, value)
        if handler is not None:
            self._commands.put_nowait(lambda: handler(value))

    async def _serve(self, stop: asyncio.Event) -> None:
        pump = asyncio.create_task(self._pump(), name="pump")
        worker = asyncio.create_task(self._command_worker(), name="commands")
        main: asyncio.Task[None] | None = None
        try:
            self.publish(schema_topic(self.id), self._schema(), retain=True)
            for name, value in self._widget_values.items():
                if self._widgets[name].kind != "button":
                    self.publish(state_topic(self.id, name), value, retain=True)
            await self._client.subscribe(cmd_topic(self.id, "*"), self._on_command)
            await self.on_start()
            main = asyncio.create_task(self.run(), name="run")
            stopper = asyncio.create_task(stop.wait())
            await asyncio.wait({main, stopper}, return_when=asyncio.FIRST_COMPLETED)
            stopper.cancel()
            if main.done():
                main.result()  # re-raise a crash; a clean return just means "idle until stopped"
                await stop.wait()
        finally:
            if main is not None and not main.done():
                main.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await main
            try:
                await self.on_stop()
            except Exception:
                self.log.exception("on_stop failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._outbox.join(), _DRAIN_TIMEOUT)
            for task in (worker, pump, *self._video):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
