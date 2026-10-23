"""F1: the tablet's screen in a laptop window. The only module that runs scrcpy.

The session follows the active path: when the path changes while a window is open, the window
is restarted on the new path; when no path supports video, the window is closed and the reason
is kept in `status_detail`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as FsPath

from tablink.adb import Adb, AdbError
from tablink.config import VideoPreset
from tablink.paths.base import Path
from tablink.proc import Process, Spawner, stop_process, subprocess_spawner

log = logging.getLogger(__name__)

TABLET_DROP_DIR = "/sdcard/Download/TabLink/"

# scrcpy exit codes: 0 = window closed by the user, 1 = could not start, 2 = device lost.
EXIT_CLOSED = 0
EXIT_DISCONNECTED = 2


class ScrcpyNotFound(RuntimeError):
    pass


def find_scrcpy() -> str:
    if env := os.environ.get("TABLINK_SCRCPY"):
        return env
    if found := shutil.which("scrcpy"):
        return found
    for p in (
        FsPath.home() / ".local" / "bin" / "scrcpy",
        FsPath.home() / ".local" / "opt" / "scrcpy" / "scrcpy",
    ):
        if p.exists():
            return str(p)
    raise ScrcpyNotFound("scrcpy not found: run scripts/install.sh or install scrcpy 4 or newer")


def build_argv(
    scrcpy: str, serial: str, preset: VideoPreset, title: str, drop_dir: str = TABLET_DROP_DIR
) -> list[str]:
    return [
        scrcpy,
        f"--serial={serial}",
        f"--max-size={preset.max_size}",
        f"--video-bit-rate={preset.bitrate}",
        f"--max-fps={preset.fps}",
        f"--window-title={title}",
        f"--push-target={drop_dir}",
        "--stay-awake",  # only while charging over USB
        # On Wi-Fi too: the tablet must not lock while it is being used from the laptop.
        # scrcpy restores the user's own timeout when the window closes.
        "--screen-off-timeout=1800",
        "--disable-screensaver",
    ]


def explain_failure(code: int, output: list[str]) -> str:
    """Turn scrcpy's exit code and last lines into one sentence for the user."""
    errors = [line.removeprefix("ERROR:").strip() for line in output if line.startswith("ERROR")]
    text = " ".join(errors).lower()
    if code == EXIT_DISCONNECTED or "disconnected" in text:
        return "The tablet disconnected."
    if "unauthorized" in text:
        return "Unlock the tablet and tap Allow on the USB debugging prompt."
    if "could not find any adb device" in text or "not found" in text:
        return "The tablet is not connected."
    if "encoder" in text or "codec" in text:
        return "The tablet could not start screen capture. Try the Low video preset."
    if errors:
        return errors[-1]
    return f"Screen session ended unexpectedly (scrcpy exit {code})."


@dataclass
class SessionEnd:
    code: int
    by_user: bool  # the user closed the window
    reason: str  # empty when closed by the user


class ScrcpyService:
    name = "scrcpy"
    needs_video = True

    def __init__(
        self,
        adb: Adb,
        presets: dict[str, VideoPreset],
        title: str = "Tablet · TabLink",
        spawner: Spawner | None = None,
        scrcpy_path: str | None = None,
        on_end: Callable[[SessionEnd], None] | None = None,
    ) -> None:
        self.adb = adb
        self.presets = presets
        self.title = title
        self._spawn: Spawner = spawner or subprocess_spawner
        self._scrcpy_path = scrcpy_path
        self.on_end = on_end
        self.path: Path | None = None
        self.wanted = False  # the user asked for a window; reopen it when a path returns
        self.status_detail = ""
        self._proc: Process | None = None
        self._watch: asyncio.Task[None] | None = None
        self._restarting = False
        self._lock = asyncio.Lock()
        self._ended: asyncio.Future[SessionEnd] | None = None

    @property
    def scrcpy_path(self) -> str:
        if self._scrcpy_path is None:
            self._scrcpy_path = find_scrcpy()
        return self._scrcpy_path

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def state(self) -> str:
        if self.running:
            return "connected"
        return "offline" if self.wanted else "disabled"

    async def on_path_changed(self, path: Path | None) -> None:
        old, self.path = self.path, path
        if not self.wanted:
            return
        if path is None or not path.video_ok:
            await self._stop()
            self.status_detail = (
                "Screen control is off: this link is too slow for video."
                if path is not None
                else "Waiting for the tablet."
            )
            return
        if (
            not self.running
            or old is None
            or (old.mode, old.adb_serial)
            != (
                path.mode,
                path.adb_serial,
            )
        ):
            await self._restart()

    async def open(self) -> None:
        """Open the tablet window on the current path (the icon click)."""
        self.wanted = True
        if self._ended is None or self._ended.done():
            self._ended = asyncio.get_running_loop().create_future()
        if self.path is None:
            self.status_detail = "Waiting for the tablet."
            return
        if not self.path.video_ok:
            self.status_detail = "Screen control is off: this link is too slow for video."
            return
        if not self.running:
            await self._restart()

    async def close(self) -> None:
        self.wanted = False
        await self._stop()
        if self._ended is not None and not self._ended.done():
            self._ended.set_result(SessionEnd(EXIT_CLOSED, by_user=True, reason=""))

    async def wait_closed(self) -> SessionEnd:
        """Wait until the window opened by `open()` closes for good: by the user, or on an error."""
        if self._ended is None:
            raise RuntimeError("no session was opened")
        return await asyncio.shield(self._ended)

    async def _restart(self) -> None:
        async with self._lock:
            self._restarting = True
            try:
                await self._stop_locked()
                await self._start_locked()
            finally:
                self._restarting = False

    async def _start_locked(self) -> None:
        path = self.path
        assert path is not None and path.adb_serial is not None
        preset_name = path.preset or "low"
        preset = self.presets[preset_name]
        with contextlib.suppress(AdbError):
            await self.adb.shell(path.adb_serial, "mkdir", "-p", TABLET_DROP_DIR)
        argv = build_argv(self.scrcpy_path, path.adb_serial, preset, self.title)
        env = {"ADB": self.adb.adb_path}
        log.info("opening tablet window via %s (%s preset)", path.mode, preset_name)
        self._proc = await self._spawn(argv, env)
        self.status_detail = f"{preset_name} quality"
        self._watch = asyncio.create_task(self._watch_proc(self._proc))

    async def _watch_proc(self, proc: Process) -> None:
        code = await proc.wait()
        if proc is not self._proc or self._restarting:
            return  # we stopped it on purpose
        self._proc = None
        by_user = code == EXIT_CLOSED
        reason = "" if by_user else explain_failure(code, proc.output_tail())
        if by_user:
            self.wanted = False
            self.status_detail = ""
        else:
            self.status_detail = reason
            log.warning("tablet window ended: %s", reason)
        end = SessionEnd(code, by_user, reason)
        if self._ended is not None and not self._ended.done():
            self._ended.set_result(end)
        if self.on_end:
            self.on_end(end)

    async def _stop(self) -> None:
        async with self._lock:
            self._restarting = True
            try:
                await self._stop_locked()
            finally:
                self._restarting = False

    async def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            await stop_process(proc)
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
            self._watch = None
