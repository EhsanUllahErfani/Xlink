"""Desktop notifications over D-Bus (org.freedesktop.Notifications), with a stderr fallback.

Used by the icon click, which has no terminal to print errors to.
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol

log = logging.getLogger(__name__)

APP_NAME = "TabLink"
ICON = "tablink"


class Notifier(Protocol):
    def __call__(self, title: str, body: str = "", urgent: bool = False) -> None: ...


def stderr_notify(title: str, body: str = "", urgent: bool = False) -> None:
    print(f"{title}{': ' + body if body else ''}", file=sys.stderr)


def desktop_notify(title: str, body: str = "", urgent: bool = False) -> None:
    """Show a GNOME notification; also print it, so terminal users see it too."""
    stderr_notify(title, body, urgent)
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = {"urgency": GLib.Variant("y", 2 if urgent else 1)}
        bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            GLib.Variant("(susssasa{sv}i)", (APP_NAME, 0, ICON, title, body, [], hints, 6000)),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
    except Exception as e:  # no session bus, no gi: the stderr line is enough
        log.debug("desktop notification failed: %s", e)
