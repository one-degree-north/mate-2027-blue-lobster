"""Shows every widget type. Delete or copy as a starting point."""

import asyncio
import math
import time

from rovkit import Button, Module, Plot, Readout, Slider, Toggle


class Demo(Module):
    title = "Widget demo"

    gain = Slider(0, 10, step=0.5, default=1, label="Gain")
    running = Toggle(default=True, label="Wave running")
    reset = Button("Reset counter")
    counter = Readout("Counter", default=0)
    wave = Plot("Sine wave", unit="V", window=150, y_min=-10, y_max=10)

    async def on_reset(self) -> None:
        self.counter = 0

    async def on_running(self, value: bool) -> None:
        self.log.info("wave %s", "running" if value else "paused")

    async def run(self) -> None:
        started = time.monotonic()
        while True:
            if self.running:
                self.counter += 1
                self.wave = self.gain * math.sin((time.monotonic() - started) * 2)
            await asyncio.sleep(0.1)
