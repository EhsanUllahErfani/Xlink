"""The one-click flow behind the TabLink icon: find the tablet, open its screen.

Until the path manager (T5) and the other paths (T6+) exist, this uses the USB path only.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import sys
from collections.abc import Iterator

from tablink import config as config_mod
from tablink.adb import Adb, AdbError, AdbNotFound
from tablink.daemon import runtime_dir
from tablink.notify import Notifier
from tablink.paths.base import Path
from tablink.paths.usb import UsbPath
from tablink.proc import Process, Spawner, stop_process, subprocess_spawner
from tablink.services.scrcpy import ScrcpyNotFound, ScrcpyService, SessionEnd

log = logging.getLogger(__name__)

# Exit codes for `tablink connect`.
OK = 0
NO_TABLET = 1
MISSING_TOOL = 4
ALREADY_OPEN = 0  # a second click while the window is open is not an error

BAR_EXIT_DISCONNECT = 10  # tablink.ui.bar.EXIT_DISCONNECT, without importing GTK here

MODE_TEXT = {
    "usb": "USB cable",
    "private": "private network",
    "lan": "Wi-Fi",
    "laptop_hotspot": "laptop hotspot",
    "tablet_hotspot": "tablet hotspot",
    "tailscale": "Tailscale",
}


@contextlib.contextmanager
def single_instance(name: str = "connect") -> Iterator[bool]:
    """Yield True if we got the lock, False if another `tablink connect` holds it."""
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(d / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


async def explain_no_usb(adb: Adb) -> str:
    try:
        devices = await adb.devices()
    except AdbError:
        return "Could not talk to adb."
    usb = [d for d in devices if d.transport == "usb"]
    if any(d.state == "unauthorized" for d in usb):
        return "Unlock the tablet and tap Allow on the “Allow USB debugging?” prompt."
    if any(d.state == "offline" for d in usb):
        return "The tablet is still starting up. Try again in a few seconds."
    return "Plug the tablet into this laptop with a USB-C cable."


async def start_bar(spawn: Spawner, path: Path, cfg: config_mod.Config) -> Process | None:
    """Start the control bar next to the tablet window. A missing bar never stops the session."""
    assert path.adb_serial
    subtitle = f"{MODE_TEXT.get(path.mode, path.mode)} · {path.preset or 'no'} quality"
    argv = [
        sys.executable,
        "-m",
        "tablink.cli",
        "bar",
        f"--serial={path.adb_serial}",
        f"--name={cfg.device.tablet_name}",
        f"--subtitle={subtitle}",
    ]
    try:
        return await spawn(argv, None)
    except OSError as e:
        log.warning("control bar unavailable: %s", e)
        return None


async def run_session(scrcpy: ScrcpyService, bar: Process | None) -> SessionEnd:
    """Wait for the tablet window to close. The bar's Disconnect button also ends it."""
    window = asyncio.ensure_future(scrcpy.wait_closed())
    try:
        while bar is not None:
            bar_done = asyncio.ensure_future(bar.wait())
            done, _ = await asyncio.wait({window, bar_done}, return_when=asyncio.FIRST_COMPLETED)
            if window in done:
                bar_done.cancel()
                break
            if bar_done.result() == BAR_EXIT_DISCONNECT:
                await scrcpy.close()
                break
            bar = None  # the user closed just the bar; keep the window
        return await window
    finally:
        if bar is not None:
            await stop_process(bar)


async def connect(
    notify: Notifier,
    adb: Adb | None = None,
    scrcpy: ScrcpyService | None = None,
    cfg: config_mod.Config | None = None,
    spawn: Spawner | None = None,
) -> int:
    cfg = cfg or config_mod.load()
    adb = adb or Adb()
    usb = UsbPath(adb, cfg.network.control_port, serial_hint=cfg.device.tablet_serial)
    try:
        path = await usb.probe()
        if path is None:
            notify("Tablet not found", await explain_no_usb(adb), urgent=True)
            return NO_TABLET
    except AdbNotFound as e:
        notify("adb is missing", str(e), urgent=True)
        return MISSING_TOOL

    if not cfg.device.tablet_serial and path.adb_serial:
        cfg.device.tablet_serial = path.adb_serial
        config_mod.save(cfg)

    try:
        await usb.activate(path)
    except AdbError as e:  # the tunnel is for other services; the screen still works without it
        log.warning("could not set up the USB tunnel: %s", e)

    scrcpy = scrcpy or ScrcpyService(adb, cfg.video, title=f"{cfg.device.tablet_name} · TabLink")
    await scrcpy.on_path_changed(path)
    try:
        await scrcpy.open()
        bar = await start_bar(spawn or subprocess_spawner, path, cfg)
        end = await run_session(scrcpy, bar)
    except ScrcpyNotFound as e:
        notify("scrcpy is missing", str(e), urgent=True)
        return MISSING_TOOL
    finally:
        await scrcpy.close()
        await usb.deactivate()
    if not end.by_user:
        notify("Tablet window closed", end.reason, urgent=True)
        return NO_TABLET
    return OK
