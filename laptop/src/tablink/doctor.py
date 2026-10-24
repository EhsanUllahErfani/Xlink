"""`tablink doctor`: check every part of TabLink and say how to fix what is wrong."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tablink import config as config_mod
from tablink import daemon
from tablink.adb import LEGACY_TCP_PORT, Adb, AdbNotFound


@dataclass
class Check:
    name: str
    ok: bool | None  # None = not applicable / informational
    detail: str
    fix: str = ""


async def _safe(fn: Callable[[], Awaitable[Check]], name: str) -> Check:
    try:
        return await fn()
    except Exception as e:  # a broken check must not hide the others
        return Check(name, False, f"check failed: {type(e).__name__}: {e}")


class Doctor:
    def __init__(self, adb: Adb | None = None) -> None:
        self.adb = adb or Adb()
        self.cfg = config_mod.load()

    async def service(self) -> Check:
        st = await daemon.get_status()
        if not st.running:
            return Check(
                "TabLink service", False, "not running", "systemctl --user restart tablinkd"
            )
        link = st.mode or "no link"
        return Check("TabLink service", True, f"running, link: {link}")

    async def usb(self) -> Check:
        try:
            devices = [d for d in await self.adb.devices() if d.transport == "usb"]
        except AdbNotFound as e:
            return Check("USB cable", False, str(e), "run scripts/install.sh")
        if any(d.state == "unauthorized" for d in devices):
            return Check(
                "USB cable", False, "tablet waiting for permission",
                'Unlock the tablet and tap "Allow" on "Allow USB debugging?"',
            )  # fmt: skip
        if not any(d.ready for d in devices):
            return Check("USB cable", None, "tablet not plugged in (fine on Wi-Fi)")
        return Check("USB cable", True, "tablet connected")

    async def wifi(self) -> Check:
        from tablink.paths.lan import tcp_reachable

        addrs = self.cfg.network.known_tablet_addrs
        if not addrs:
            return Check(
                "Wi-Fi link", False, "tablet address not known yet",
                "Plug the tablet in by USB once (TabLink learns it), or run tablink pair",
            )  # fmt: skip
        for a in addrs:
            if await tcp_reachable(a, LEGACY_TCP_PORT):
                return Check("Wi-Fi link", True, f"tablet answers at {a}")
        return Check(
            "Wi-Fi link", False, "tablet not reachable on Wi-Fi",
            "Put both on the same Wi-Fi. If the tablet restarted, plug in USB once "
            "(Android turns adb-over-Wi-Fi off at every restart).",
        )  # fmt: skip

    async def remote_desktop(self) -> Check:
        from tablink.services.remote_desktop import RemoteDesktop

        st = await RemoteDesktop().status()
        if st.ready:
            return Check("Remote desktop", True, f"on, port {st.port}")
        return Check("Remote desktop", False, "not set up", "tablink pair")

    async def pairing(self) -> Check:
        from tablink.secrets import KeyringStore

        token = await asyncio.to_thread(KeyringStore().get)
        if token:
            return Check("Pairing", True, "secret in your keyring")
        return Check("Pairing", False, "not paired", "tablink pair")

    async def clipboard(self) -> Check:
        from tablink.services.clipboard import ShellBridge

        if await asyncio.to_thread(ShellBridge().get_text) is not None:
            return Check("Clipboard sharing", True, "extension loaded")
        return Check(
            "Clipboard sharing", False, "GNOME extension not loaded",
            "Log out of Ubuntu and back in once (run tablink pair first if you never did)",
        )  # fmt: skip

    async def screen_tool(self) -> Check:
        from tablink.services.scrcpy import ScrcpyNotFound, find_scrcpy

        try:
            path = find_scrcpy()
        except ScrcpyNotFound as e:
            return Check("Tablet window (scrcpy)", False, str(e), "run scripts/install.sh")
        return Check("Tablet window (scrcpy)", True, path)

    async def internet(self) -> Check:
        if not self.cfg.tailscale.enabled:
            return Check("Internet access", None, "off", "tablink internet on (optional)")
        try:
            reply = await daemon.request({"cmd": "internet", "action": "status"}, timeout=30.0)
        except (OSError, TimeoutError, ValueError):
            return Check("Internet access", False, "service not running")
        msg = str(reply.get("message", ""))
        if "is on" in msg and "not signed in" not in msg:
            return Check("Internet access", True, msg)
        if reply.get("url"):
            return Check("Internet access", False, "laptop not signed in", "tablink internet on")
        return Check(
            "Internet access", False, msg,
            "Open TabLink on the tablet and tap Sign in (same account as the laptop)",
        )  # fmt: skip

    async def run(self) -> list[Check]:
        checks = [
            (self.service, "TabLink service"),
            (self.usb, "USB cable"),
            (self.wifi, "Wi-Fi link"),
            (self.pairing, "Pairing"),
            (self.remote_desktop, "Remote desktop"),
            (self.clipboard, "Clipboard sharing"),
            (self.screen_tool, "Tablet window (scrcpy)"),
            (self.internet, "Internet access"),
        ]
        return [await _safe(fn, name) for fn, name in checks]
