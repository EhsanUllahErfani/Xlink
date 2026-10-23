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
from typing import TYPE_CHECKING, Any

from tablink import __version__
from tablink.control.server import ControlServer
from tablink.paths.base import Path as TabletPath
from tablink.paths.manager import PathManager
from tablink.paths.usb import UsbPath

if TYPE_CHECKING:
    from tablink.screen import ScreenSession

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
    serial: str | None = None  # adb serial on the active path
    version: str = __version__
    services: list[ServiceStatus] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DaemonStatus:
        return cls(
            running=bool(d.get("running", True)),
            mode=d.get("mode"),
            rtt_ms=d.get("rtt_ms"),
            serial=d.get("serial"),
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
    """tablinkd: local socket for the CLI, the path manager, and the tablet's control channel."""

    def __init__(
        self,
        sock: Path | None = None,
        control: ControlServer | None = None,
        manager: PathManager | None = None,
        screen: ScreenSession | None = None,
    ) -> None:
        self.sock = sock or socket_path()
        self.status = DaemonStatus(running=True)
        self.control = control
        self.manager = manager
        self.screen = screen
        self._server: asyncio.Server | None = None
        self._status_task: asyncio.Task[None] | None = None

    @property
    def path(self) -> TabletPath | None:
        return self.manager.active if self.manager else None

    def refresh_status(self) -> None:
        if self.manager is None:
            return  # nothing to read from (tests set the status directly)
        p = self.path
        self.status.mode = p.mode if p else None
        self.status.rtt_ms = p.rtt_ms if p else None
        self.status.serial = p.adb_serial if p else None
        self.status.services = []
        if self.screen is not None:
            svc = self.screen.scrcpy
            self.status.services.append(ServiceStatus("screen", svc.state, svc.status_detail))

    async def _local(self, msg: dict[str, Any]) -> dict[str, Any]:
        cmd = msg.get("cmd")
        if cmd == "status":
            self.refresh_status()
            return asdict(self.status)
        if cmd == "open_screen" and self.screen is not None:
            return {"ok": True, "message": await self.screen.open()}
        if cmd == "close_screen" and self.screen is not None:
            await self.screen.close()
            return {"ok": True, "message": "Closed."}
        return {"error": f"unknown command: {cmd!r}"}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                msg = json.loads(line)
                reply = await self._local(msg) if isinstance(msg, dict) else {"error": "bad"}
            except ValueError:
                reply = {"error": "bad message"}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def start(self) -> None:
        self.sock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sock.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.sock))
        os.chmod(self.sock, 0o600)
        if self.control is not None:
            await self.control.start()
        if self.manager is not None:
            self.manager.start()
        log.info("tablinkd %s listening on %s", __version__, self.sock)

    async def stop(self) -> None:
        if self.screen is not None:
            with contextlib.suppress(Exception):
                await self.screen.close()
        if self.manager is not None:
            await self.manager.stop()
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
    """The real daemon, wired to adb, the path providers, the screen and remote desktop."""
    from tablink import config as config_mod
    from tablink.adb import Adb
    from tablink.notify import desktop_notify
    from tablink.paths.lan import LanPath
    from tablink.proc import subprocess_spawner
    from tablink.remote_session import bookmark_ref, prepare_remote, show_laptop_on_tablet
    from tablink.screen import ScreenSession
    from tablink.services.scrcpy import ScrcpyService

    cfg = config_mod.load()
    adb = Adb()

    def learn(addrs: list[str]) -> None:
        current = config_mod.load()
        before = list(current.network.known_tablet_addrs)
        for a in reversed(addrs):
            current.remember_tablet_addr(a)
        if current.network.known_tablet_addrs != before:
            config_mod.save(current)
            log.info("learned tablet Wi-Fi address")

    usb = UsbPath(
        adb,
        cfg.network.control_port,
        serial_hint=cfg.device.tablet_serial,
        prepare_wifi=True,
        on_wifi_addrs=learn,
    )
    lan = LanPath(
        adb,
        lambda: config_mod.load().network.known_tablet_addrs,
        control_port=cfg.network.control_port,
    )
    scrcpy = ScrcpyService(adb, cfg.video, title=f"{cfg.device.tablet_name} · TabLink")
    screen = ScreenSession(scrcpy, subprocess_spawner, cfg, desktop_notify)
    manager = PathManager([usb, lan], [screen])
    holder: dict[str, Daemon] = {}

    async def remote(msg: dict[str, Any]) -> dict[str, Any]:
        path = holder["d"].path
        if path is None or path.adb_serial is None:
            return {"type": "remote", "ok": False, "message": "The tablet is not connected."}
        bookmark = config_mod.load().remote.afreerdp_bookmark
        if bookmark:
            # The tablet app opens the saved connection itself: no login box, nothing typed.
            await prepare_remote(adb, path.adb_serial)
            return {
                "type": "remote",
                "ok": True,
                "message": "Opening the laptop…",
                "conRef": bookmark_ref(bookmark),
            }
        text = await show_laptop_on_tablet(adb, path.adb_serial)
        return {
            "type": "remote",
            "ok": text.startswith(("The laptop", "aFreeRDP")),
            "message": text,
        }

    async def open_screen(msg: dict[str, Any]) -> dict[str, Any]:
        return {"type": "screen", "ok": True, "message": await screen.open()}

    def hello() -> dict[str, Any]:
        path = holder["d"].path
        return {"mode": path.mode if path else None}

    control = ControlServer(
        cfg.network.control_port,
        {"remote_ready": remote, "screen": open_screen},
        hello_extra=hello,
    )
    d = Daemon(control=control, manager=manager, screen=screen)
    holder["d"] = d
    return d


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(build().run_forever())
