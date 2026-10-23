"""The tablet window as a daemon-owned service: the scrcpy window plus its control bar.

Because the path manager tells this service about every path change, pulling the USB cable
mid-session moves the window to Wi-Fi by itself (and back when the cable returns).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any

from tablink import config as config_mod
from tablink.launcher import BAR_EXIT_DISCONNECT, start_bar
from tablink.notify import Notifier
from tablink.paths.base import Path
from tablink.proc import Process, Spawner, stop_process
from tablink.services.scrcpy import ScrcpyService

log = logging.getLogger(__name__)


class ScreenSession:
    name = "screen"
    needs_video = True

    def __init__(
        self,
        scrcpy: ScrcpyService,
        spawn: Spawner,
        cfg: config_mod.Config,
        notify: Notifier,
    ) -> None:
        self.scrcpy = scrcpy
        self.spawn = spawn
        self.cfg = cfg
        self.notify = notify
        self.path: Path | None = None
        self.bar: Process | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def open_(self) -> bool:
        return self.scrcpy.wanted

    def _bg(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_path_changed(self, path: Path | None) -> None:
        old, self.path = self.path, path
        await self.scrcpy.on_path_changed(path)
        if not self.scrcpy.wanted:
            return
        moved = old is None or path is None or old.adb_serial != path.adb_serial
        if moved:
            await self._stop_bar()
            if path is not None and path.video_ok:
                await self._start_bar()

    async def open(self) -> str:
        if self.scrcpy.wanted and self.scrcpy.running:
            return "The tablet window is already open."
        if self.path is None:
            return "Tablet not found. Plug in the USB cable or join the same Wi-Fi."
        if not self.path.video_ok:
            return "Screen control is off: this link is too slow for video."
        await self.scrcpy.open()
        await self._start_bar()
        self._bg(self._watch_window())
        return "Opening the tablet on the laptop."

    async def close(self) -> None:
        await self.scrcpy.close()
        await self._stop_bar()

    async def _start_bar(self) -> None:
        if self.path is None:
            return
        self.bar = await start_bar(self.spawn, self.path, self.cfg)
        if self.bar is not None:
            self._bg(self._watch_bar(self.bar))

    async def _stop_bar(self) -> None:
        bar, self.bar = self.bar, None
        if bar is not None:
            await stop_process(bar)

    async def _watch_bar(self, bar: Process) -> None:
        code = await bar.wait()
        if bar is self.bar and code == BAR_EXIT_DISCONNECT:
            await self.close()

    async def _watch_window(self) -> None:
        with contextlib.suppress(RuntimeError):
            end = await self.scrcpy.wait_closed()
            await self._stop_bar()
            if not end.by_user:
                self.notify("Tablet window closed", end.reason, True)
