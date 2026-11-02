"""Copy this file to modules/<your_module>.py (lowercase, underscores). Files starting
with ``_`` are ignored by the loader, so this one never runs.

Run everything with ``uv run rovkit run --reload``; saving this file restarts the module.
"""

import asyncio

from rovkit import Button, Module, Readout, Slider


class MyModule(Module):
    title = "My module"  # panel title in the UI

    # Widgets show up in the UI automatically, in this order.
    threshold = Slider(0, 100, default=50, unit="%")
    go = Button("Do the thing")
    result = Readout("Result")

    async def on_start(self) -> None:
        """Open cameras, serial ports, etc."""

    async def on_threshold(self, value: float) -> None:
        """Called when the slider moves; self.threshold is already updated."""

    async def on_go(self) -> None:
        """Called when the button is clicked."""
        self.result = f"threshold was {self.threshold}"

    async def run(self) -> None:
        """Optional main loop. Delete it if the module only reacts to the UI."""
        ticks = 0
        while True:
            ticks += 1
            self.result = f"{ticks} seconds up"
            await asyncio.sleep(1)

    async def on_stop(self) -> None:
        """Release hardware."""
