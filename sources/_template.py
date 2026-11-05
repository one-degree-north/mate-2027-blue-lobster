"""Copy this file to sources/<your_source>.py. Files starting with ``_`` are ignored.

A source is where video enters: a camera, a file, a test pattern. It runs like a module but
has no panel and no widgets. Modules read its frames with ``self.frames("<stream name>")``.
"""

import asyncio

from rovkit import Source
from rovkit.video import encode_jpeg


class MySource(Source):
    async def run(self) -> None:
        stream = self.video("mine")  # the name modules and the UI use
        self.report("live")  # shown in the UI header
        while True:
            stream.push_jpeg(encode_jpeg(grab_frame()))
            await asyncio.sleep(1 / 15)


def grab_frame():  # replace with real capture; return a PIL image or an RGB numpy array
    raise NotImplementedError
