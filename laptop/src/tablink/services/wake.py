"""Waking the laptop: turn its screen back on when the tablet asks, and tell the tablet how to
send a Wake-on-Wi-Fi magic packet (when the laptop is asleep on the same network).
"""

from __future__ import annotations

import contextlib
import logging
import socket
from pathlib import Path

from tablink.paths.netinfo import default_gateways, local_addr_towards

log = logging.getLogger(__name__)


def wake_screen() -> bool:
    """Ask GNOME to end the screen blank (shows the lock screen if locked). Blocking."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver",
            "SetActive", GLib.Variant("(b)", (False,)), None, Gio.DBusCallFlags.NONE, 2000, None,
        )  # fmt: skip
        return True
    except Exception as e:
        log.info("could not wake the screen: %s", type(e).__name__)
        return False


def default_iface(route_file: Path = Path("/proc/net/route")) -> str | None:
    try:
        for line in route_file.read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":
                return f[0]
    except OSError:
        pass
    return None


def wake_info(sys_net: Path = Path("/sys/class/net")) -> dict[str, str]:
    """MAC and address the tablet needs to wake this laptop from sleep (not secret)."""
    iface = default_iface()
    if iface is None:
        return {}
    try:
        mac = (sys_net / iface / "address").read_text().strip()
    except OSError:
        return {}
    info = {"mac": mac}
    gws = default_gateways()
    addr = local_addr_towards(gws[0]) if gws else None
    if addr:
        info["ip"] = addr
    with contextlib.suppress(OSError):
        info["host"] = socket.gethostname()
    return info
