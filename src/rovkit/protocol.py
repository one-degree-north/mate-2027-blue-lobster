"""Wire protocol shared by the hub, Python modules, and (by convention) the browser UI.

Every frame is one JSON object with a ``type`` discriminator. Clients send
:class:`Publish`, :class:`Subscribe` and :class:`Unsubscribe`; the hub sends
:class:`Message`. Payloads under ``data`` are arbitrary JSON.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
WS_PATH = "/ws"


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Publish(_Frame):
    """Client -> hub. ``retain`` keeps the value so late subscribers receive it."""

    type: Literal["publish"] = "publish"
    topic: str = Field(min_length=1)
    data: Any = None
    retain: bool = False


class Subscribe(_Frame):
    """Client -> hub. Patterns are ``fnmatch`` globs, e.g. ``demo/*``."""

    type: Literal["subscribe"] = "subscribe"
    patterns: list[str] = Field(min_length=1)


class Unsubscribe(_Frame):
    type: Literal["unsubscribe"] = "unsubscribe"
    patterns: list[str] = Field(min_length=1)


class Message(_Frame):
    """Hub -> client. ``ts`` is stamped by the hub (seconds since the epoch)."""

    type: Literal["message"] = "message"
    topic: str
    ts: float
    data: Any = None


class Error(_Frame):
    """Hub -> client. Sent when a frame could not be parsed; the connection stays open."""

    type: Literal["error"] = "error"
    detail: str


ClientFrame = Annotated[Publish | Subscribe | Unsubscribe, Field(discriminator="type")]

client_frame_adapter: TypeAdapter[Publish | Subscribe | Unsubscribe] = TypeAdapter(ClientFrame)


# -- topic conventions -------------------------------------------------------------
#
#   modules/<id>/schema        retained  widget layout, published by the module
#   modules/<id>/state/<w>     retained  current widget value, published by the module
#   modules/<id>/cmd/<w>       one-shot  UI input (click or new value), consumed by the module
#   modules/<id>/disabled      retained  ids of the widgets the module currently refuses input for
#   modules/<id>/status        retained  process state, published by the loader
#   modules/_index             retained  ids of every module the loader started (the UI drops
#                                        saved panels for modules that are not listed)
#   sources/<id>/status        retained  process state of a video source, published by the loader
#   sources/<id>/info          retained  what the source reports about itself (live, fps, ...)
#   video/<stream>/meta        retained  announces a stream; frames travel over HTTP/WS, not here
#   video/<stream>/overlay     one-shot  detections to draw over the stream

MODULE_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
MODULE_INDEX_TOPIC = "modules/_index"  # "_" cannot start a module id, so this never clashes


def schema_topic(module_id: str) -> str:
    return f"modules/{module_id}/schema"


def state_topic(module_id: str, widget: str) -> str:
    return f"modules/{module_id}/state/{widget}"


def cmd_topic(module_id: str, widget: str) -> str:
    return f"modules/{module_id}/cmd/{widget}"


def disabled_topic(module_id: str) -> str:
    return f"modules/{module_id}/disabled"


def status_topic(module_id: str, kind: str = "module") -> str:
    """Where the loader reports a process: ``modules/<id>/status`` or ``sources/<id>/status``."""
    return f"{'sources' if kind == 'source' else 'modules'}/{module_id}/status"


def source_info_topic(source_id: str) -> str:
    return f"sources/{source_id}/info"
