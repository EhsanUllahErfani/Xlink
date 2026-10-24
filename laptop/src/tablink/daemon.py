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
from collections.abc import Awaitable, Callable
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


async def request(
    msg: dict[str, Any], path: Path | None = None, timeout: float = REQUEST_TIMEOUT_S
) -> Any:
    """Send one request to the daemon and return its reply. Raises OSError if it is not running."""
    path = path or socket_path()
    async with asyncio.timeout(timeout):
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
        background: list[Any] | None = None,
    ) -> None:
        self.background = background or []  # objects with start() / async stop()
        self.local_handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {}
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
        if cmd in self.local_handlers:
            return await self.local_handlers[cmd](msg)
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
        for b in self.background:
            b.start()
        log.info("tablinkd %s listening on %s", __version__, self.sock)

    async def stop(self) -> None:
        for b in self.background:
            with contextlib.suppress(Exception):
                await b.stop()
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


class _StopOnly:
    """Background entry for parts that start on first use and only need stopping."""

    def __init__(self, part: Any) -> None:
        self.part = part

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self.part.stop()


def build() -> Daemon:
    """The real daemon, wired to adb, the path providers, the screen and remote desktop."""
    from tablink import config as config_mod
    from tablink.adb import Adb
    from tablink.nmcli import Nmcli, NmError
    from tablink.nmcli import new_password as new_hotspot_password
    from tablink.notify import desktop_notify, private_notify
    from tablink.paths.lan import LanPath
    from tablink.paths.tailscale import TailscalePath
    from tablink.proc import subprocess_spawner
    from tablink.remote_session import prepare_remote
    from tablink.screen import ScreenSession
    from tablink.secrets import KeyringStore, control_token, get_or_create
    from tablink.services.clipboard import ClipboardSync, ShellBridge
    from tablink.services.files import FileReceiver
    from tablink.services.scrcpy import ScrcpyService
    from tablink.services.wake import wake_info, wake_screen
    from tablink.tailscale import Tailscale, find_tablet

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
    nm = Nmcli()

    async def hotspot_iface() -> str | None:
        try:
            return await nm.hotspot_device()
        except Exception:  # NetworkManager busy or missing: treat as no hotspot
            return None

    lan = LanPath(
        adb,
        lambda: config_mod.load().network.known_tablet_addrs,
        hotspot_iface=hotspot_iface,
        control_port=cfg.network.control_port,
    )
    scrcpy = ScrcpyService(adb, cfg.video, title=f"{cfg.device.tablet_name} · TabLink")
    screen = ScreenSession(scrcpy, subprocess_spawner, cfg, desktop_notify)
    tailnet = Tailscale(KeyringStore("tailnet-state"))
    ts_path = TailscalePath(
        adb,
        tailnet,
        enabled=lambda: config_mod.load().tailscale.enabled,
        name_hint=lambda: config_mod.load().device.tablet_name,
        control_port=cfg.network.control_port,
    )
    manager = PathManager([usb, lan, ts_path], [screen])
    holder: dict[str, Any] = {}

    async def remote(msg: dict[str, Any]) -> dict[str, Any]:
        path = holder["d"].path
        if path is None or path.adb_serial is None:
            return {"type": "remote", "ok": False, "message": "The tablet is not connected."}
        # The tablet opens the session itself (FreeRDP is built into TabLink); the laptop
        # only makes sure remote desktop is on and tunnelled.
        await prepare_remote(adb, path.adb_serial)
        return {"type": "remote", "ok": True, "message": "Opening the laptop…"}

    async def open_screen(msg: dict[str, Any]) -> dict[str, Any]:
        return {"type": "screen", "ok": True, "message": await screen.open()}

    keyring = KeyringStore()
    clipboard = ClipboardSync(
        ShellBridge(), lambda text: holder["control"].broadcast({"type": "clip", "text": text})
    )

    async def clip(msg: dict[str, Any]) -> dict[str, Any]:
        text = msg.get("text")
        if not isinstance(text, str):
            return {"type": "clip", "ok": False, "message": "no text"}
        if not await clipboard.from_tablet(text):
            return {
                "type": "clip",
                "ok": False,
                "message": "Log out of the laptop and back in once to turn on clipboard sharing.",
            }
        return {"type": "clip", "ok": True}

    async def notification(msg: dict[str, Any]) -> dict[str, Any]:
        app, title, text = (str(msg.get(k) or "")[:300] for k in ("app", "title", "text"))
        heading = f"{app}: {title}" if app and title else (title or app or "Tablet")
        await asyncio.to_thread(private_notify, heading, text)
        return {"type": "notification", "ok": True}

    async def wake_laptop(msg: dict[str, Any]) -> dict[str, Any]:
        ok = await asyncio.to_thread(wake_screen)
        return {
            "type": "wake_laptop",
            "ok": ok,
            "message": "The laptop screen is on." if ok else "Could not turn the screen on.",
        }

    def hello() -> dict[str, Any]:
        path = holder["d"].path
        # The wake details let the tablet send a magic packet later, when the laptop sleeps.
        return {
            "mode": path.mode if path else None,
            **{f"wake_{k}": v for k, v in wake_info().items()},
        }

    control = ControlServer(
        cfg.network.control_port,
        {
            "remote_ready": remote,
            "screen": open_screen,
            "clip": clip,
            "notification": notification,
            "wake_laptop": wake_laptop,
        },
        hello_extra=hello,
        token=lambda: control_token(keyring),
        files=FileReceiver(),
    )
    d = Daemon(
        control=control, manager=manager, screen=screen, background=[clipboard, _StopOnly(tailnet)]
    )
    holder["d"] = d

    async def hotspot_on(msg: dict[str, Any]) -> dict[str, Any]:
        ssid = config_mod.load().network.hotspot_ssid
        password = get_or_create(KeyringStore("hotspot-password"), new_hotspot_password)
        # Tell the tablet first, while it can still be reached on the current link.
        told = await control.broadcast({"type": "wifi", "ssid": ssid, "psk": password})
        try:
            dev = await nm.hotspot_on(ssid, password)
        except NmError as e:
            return {"ok": False, "message": f"Could not start the hotspot: {e}"}
        await manager.check()
        text = f"Hotspot \u201c{ssid}\u201d is on ({dev})."
        if told:
            text += " The tablet saved it and joins by itself when no other Wi-Fi is around."
        else:
            text += (
                " The tablet was not linked: run `tablink hotspot password` to see the password."
            )
        return {"ok": True, "message": text}

    async def hotspot_off(msg: dict[str, Any]) -> dict[str, Any]:
        try:
            was_on = await nm.hotspot_off()
        except NmError as e:
            return {"ok": False, "message": f"Could not stop the hotspot: {e}"}
        await manager.check()
        return {"ok": True, "message": "Hotspot is off." if was_on else "Hotspot was not on."}

    async def force_path(msg: dict[str, Any]) -> dict[str, Any]:
        mode = msg.get("mode")
        if mode is not None and mode not in {p.mode for p in manager.providers}:
            return {"ok": False, "message": f"unknown mode: {mode}"}
        manager.forced_mode = mode
        path = await manager.check()
        if mode is None:
            return {"ok": True, "message": "Automatic link choice is back on."}
        if path is None:
            return {"ok": False, "message": f"Pinned to {mode}, but that link does not work now."}
        return {"ok": True, "message": f"Pinned to {mode} ({path.rtt_ms:.0f} ms)."}

    async def internet(msg: dict[str, Any]) -> dict[str, Any]:
        action = msg.get("action")
        current = config_mod.load()
        if action == "off":
            current.tailscale.enabled = False
            config_mod.save(current)
            await tailnet.stop()
            await manager.check()
            return {"ok": True, "message": "Internet access is off."}
        if action == "on":
            current.tailscale.enabled = True
            config_mod.save(current)
        st = await tailnet.status() if current.tailscale.enabled else None
        if st is None:
            return {"ok": True, "message": "Internet access is off (tablink internet on)."}
        if st.running:
            ip = st.self_ips[0] if st.self_ips else "?"
            tab = find_tablet(st, current.device.tablet_name)
            where = f"tablet at {tab.ips[0]}" if tab else "tablet not signed in yet"
            return {"ok": True, "message": f"Internet access is on ({ip}); {where}."}
        url = st.auth_url or await tailnet.login()
        return {
            "ok": True,
            "message": "Sign in once to turn on internet access:",
            "url": url,
        }

    d.local_handlers = {
        "internet": internet,
        "hotspot_on": hotspot_on,
        "hotspot_off": hotspot_off,
        "force_path": force_path,
    }
    holder["control"] = control
    return d


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(build().run_forever())
