"""Base class for video sources: a camera, a file player, a test pattern.

    class Webcam(Source):
        async def run(self):
            stream = self.video("camera")
            while True:
                stream.push_jpeg(await grab_jpeg())

A source runs like a module (own process, restarted if it crashes) but has no panel.
It is the one place frames enter; modules read them with ``self.frames("camera")``.
The UI lists every source in its header with the state it reports.
"""

from __future__ import annotations

from typing import Any

from .module import Module
from .protocol import source_info_topic


class Source(Module):
    """A producer of video. Put the file in ``sources/``; it cannot declare widgets."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls._widgets:
            raise TypeError(
                f"{cls.__name__}: a Source has no panel, so it cannot declare widgets "
                f"({list(cls._widgets)}). Use a Module for controls."
            )

    async def _announce(self) -> None:
        """Sources publish no schema, so the UI never builds a panel for them."""

    def report(self, state: str, **details: Any) -> None:
        """Tell the UI how the source is doing, e.g. ``self.report("live", fps=29.8)``."""
        self.publish(source_info_topic(self.id), {"state": state, **details}, retain=True)
