"""tablinkd: the background daemon, plus the local socket the CLI and tray use to talk to it.

The local socket lives in $XDG_RUNTIME_DIR (mode 0700, user-only) and speaks one JSON object per
line. Path management and services are wired in from T4/T5 onwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tablink import __version__
from tablink.control.server import ControlServer
from tablink.paths.usb import UsbPath

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 2.0


@dataclass
class ServiceStatus:
    name: str
    state: str  # "connected" | "syncing" | "offline" | "disabled"
    detail: str = ""


@dataclass
class DaemonStatus:
    running: bool
    mode: str | None = None  # active path mode, None when no path
    rtt_ms: float | None = None
    version: str = __version__
    services: list[ServiceStatus] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DaemonStatus:
        return cls(
            running=bool(d.get("running", True)),
            mode=d.get("mode"),
            rtt_ms=d.get("rtt_ms"),
            version=str(d.get("version", "")),
            services=[ServiceStatus(**s) for s in d.get("services", [])],
        )


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/tablink-{os.getuid()}"
    return Path(base) / "tablink"


def socket_path() -> Path:
    return runtime_dir() / "tablinkd.sock"


async def request(msg: dict[str, Any], path: Path | None = None) -> Any:
    """Send one request to the daemon and return its reply. Raises OSError if it is not running."""
    path = path or socket_path()
    async with asyncio.timeout(REQUEST_TIMEOUT_S):
        reader, writer = await asyncio.open_unix_connection(str(path))
        try:
            writer.write(json.dumps(msg).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
    if not line:
        raise ConnectionError("daemon closed the connection")
    return json.loads(line)


async def get_status(path: Path | None = None) -> DaemonStatus:
    try:
        reply = await request({"cmd": "status"}, path)
    except (OSError, TimeoutError, ValueError):
        return DaemonStatus(running=False)
    return DaemonStatus.from_dict(reply)


class Daemon:
    """tablinkd: local status socket, USB watcher, and the control channel for the tablet app."""

    def __init__(
        self,
        sock: Path | None = None,
        control: ControlServer | None = None,
        usb: UsbPath | None = None,
        poll_s: float = 5.0,
    ) -> None:
        self.sock = sock or socket_path()
        self.status = DaemonStatus(running=True)
        self.control = control
        self.usb = usb
        self.poll_s = poll_s
        self.serial: str | None = None
        self._server: asyncio.Server | None = None
        self._watch: asyncio.Task[None] | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                msg = json.loads(line)
                cmd = msg.get("cmd") if isinstance(msg, dict) else None
            except ValueError:
                cmd = None
            if cmd == "status":
                reply: dict[str, Any] = asdict(self.status)
            else:
                reply = {"error": f"unknown command: {cmd!r}"}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def check_usb(self) -> None:
        """One health check: tunnel the ports when the tablet is plugged in."""
        if self.usb is None:
            return
        path = await self.usb.probe()
        if path is None:
            if self.serial is not None:
                log.info("tablet unplugged")
                await self.usb.deactivate()
            self.serial = None
            self.status.mode, self.status.rtt_ms = None, None
            return
        if path.adb_serial != self.serial:
            await self.usb.activate(path)
            self.serial = path.adb_serial
            log.info("tablet connected by USB")
        self.status.mode, self.status.rtt_ms = path.mode, path.rtt_ms

    async def _watch_usb(self) -> None:
        while True:
            try:
                await self.check_usb()
            except Exception:  # a flaky adb must never kill the daemon
                log.exception("USB health check failed")
            await asyncio.sleep(self.poll_s)

    async def start(self) -> None:
        self.sock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sock.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.sock))
        os.chmod(self.sock, 0o600)
        if self.control is not None:
            await self.control.start()
        if self.usb is not None:
            self._watch = asyncio.create_task(self._watch_usb())
        log.info("tablinkd %s listening on %s", __version__, self.sock)

    async def stop(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
        if self.control is not None:
            await self.control.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self.sock.unlink(missing_ok=True)

    async def run_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()


def build() -> Daemon:
    """The real daemon, wired to adb, the screen launcher and GNOME Remote Desktop."""
    import sys

    from tablink import config as config_mod
    from tablink.adb import Adb
    from tablink.proc import Process, subprocess_spawner
    from tablink.remote_session import bookmark_ref, prepare_remote, show_laptop_on_tablet

    cfg = config_mod.load()
    adb = Adb()
    usb = UsbPath(adb, cfg.network.control_port, serial_hint=cfg.device.tablet_serial)
    holder: dict[str, Daemon] = {}
    screens: list[Process] = []

    async def remote(msg: dict[str, Any]) -> dict[str, Any]:
        d = holder["d"]
        if d.serial is None:
            return {"type": "remote", "ok": False, "message": "Tablet is not connected by USB."}
        bookmark = config_mod.load().remote.afreerdp_bookmark
        if bookmark:
            # The tablet app opens the saved connection itself: no login box, nothing typed.
            await prepare_remote(adb, d.serial)
            return {
                "type": "remote",
                "ok": True,
                "message": "Opening the laptop…",
                "conRef": bookmark_ref(bookmark),
            }
        text = await show_laptop_on_tablet(adb, d.serial)
        return {
            "type": "remote",
            "ok": text.startswith(("The laptop", "aFreeRDP")),
            "message": text,
        }

    async def screen(msg: dict[str, Any]) -> dict[str, Any]:
        screens[:] = [p for p in screens if p.returncode is None]
        if screens:
            return {"type": "screen", "ok": True, "message": "The tablet window is already open."}
        screens.append(await subprocess_spawner([sys.executable, "-m", "tablink.cli", "connect"]))
        return {"type": "screen", "ok": True, "message": "Opening the tablet on the laptop."}

    control = ControlServer(cfg.network.control_port, {"remote_ready": remote, "screen": screen})
    d = Daemon(control=control, usb=usb)
    holder["d"] = d
    return d


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(build().run_forever())
