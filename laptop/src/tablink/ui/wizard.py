"""The setup window (T17): pair the tablet over Wi-Fi by QR code (no USB), then set everything up.

Same steps as `tablink pair`, shown as they happen.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Coroutine
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from tablink.adb import Adb, AdbError  # noqa: E402
from tablink.pairing import PairError, Pairing  # noqa: E402
from tablink.wifi_pairing import (  # noqa: E402
    WifiPairError,
    connect_after_pairing,
    new_qr,
    pair_with_code,
    wait_and_pair_qr,
)

ICONS = {
    "run": "content-loading-symbolic",
    "wait": "dialog-information-symbolic",
    "ok": "emblem-ok-symbolic",
    "skip": "emblem-ok-symbolic",
    "fail": "dialog-error-symbolic",
}

STEPS = (
    "<b>On the tablet</b> (same Wi-Fi as this laptop):\n"
    "1. Settings &gt; About tablet: tap <b>OS version</b> 7 times (once).\n"
    "2. Settings &gt; Additional settings &gt; Developer options: turn on "
    "<b>Wireless debugging</b>, <b>USB debugging (Security settings)</b> and "
    "<b>Install via USB</b>.\n"
    "3. Tap <b>Wireless debugging &gt; Pair device with QR code</b> and scan:"
)

DONE = (
    "<b>All set.</b> On the tablet, open <b>TabLink</b> and follow its short checklist.\n"
    "Tip: for unlocking without a pattern while near this laptop, add this laptop's "
    "Bluetooth under the tablet's <b>Passwords &amp; security &gt; Smart Lock &gt; Trusted "
    "devices</b>."
)


class Wizard(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Set up TabLink")
        self.set_default_size(620, 820)
        self.adb = Adb()
        self.qr = new_qr()
        self._tasks: set[asyncio.Task[None]] = set()

        self.status = Gtk.Label(label="Waiting for the tablet to scan…", wrap=True)
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.qr.png(scale=10)))
        qr = Gtk.Picture.new_for_paintable(texture)
        qr.set_size_request(300, 300)
        qr.set_can_shrink(True)
        self.pair_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.pair_box.append(Gtk.Label(label=STEPS, use_markup=True, wrap=True, xalign=0))
        self.pair_box.append(qr)
        self.pair_box.append(self.status)
        self.pair_box.append(self._code_section())

        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.done = Gtk.Label(label=DONE, use_markup=True, wrap=True, xalign=0, visible=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(24)
        box.append(self.pair_box)
        box.append(self.rows)
        box.append(self.done)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, vexpand=True))
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        self._spawn(self._start())

    def _code_section(self) -> Gtk.Widget:
        exp = Gtk.Expander(label="The QR code doesn't work? Use a 6-digit code instead")
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hint = Gtk.Label(
            label="On the tablet: Wireless debugging > Pair device with pairing code.",
            wrap=True,
            xalign=0,
        )
        self.addr = Gtk.Entry(placeholder_text="IP address & port, e.g. 192.168.1.5:37011")
        self.code = Gtk.Entry(placeholder_text="6-digit code", max_length=6)
        btn = Gtk.Button(label="Pair", css_classes=["suggested-action"])
        btn.connect("clicked", lambda *_: self._spawn(self._pair_code()))
        for w in (hint, self.addr, self.code, btn):
            grid.append(w)
        exp.set_child(grid)
        return exp

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def progress(self, state: str, text: str) -> None:
        row = Adw.ActionRow(title=GLib.markup_escape_text(text), title_lines=4)
        row.add_prefix(Gtk.Image(icon_name=ICONS.get(state, "emblem-default-symbolic")))
        self.rows.append(row)

    async def _start(self) -> None:
        # Already connected (Wi-Fi from before, or USB)? Skip pairing.
        with contextlib.suppress(AdbError):
            if any(d.ready for d in await self.adb.devices()):
                self.pair_box.set_visible(False)
                await self._setup()
                return
        try:
            host = await wait_and_pair_qr(self.adb, self.qr, timeout=600)
        except WifiPairError as e:
            self.status.set_label(str(e))
            return
        await self._after_pairing(host)

    async def _pair_code(self) -> None:
        try:
            h, _, p = self.addr.get_text().strip().rpartition(":")
            host = await pair_with_code(self.adb, h, int(p), self.code.get_text().strip())
        except (ValueError, WifiPairError, AdbError) as e:
            self.status.set_label(f"Pairing failed: {e}")
            return
        await self._after_pairing(host)

    async def _after_pairing(self, host: str) -> None:
        self.status.set_label("Paired. Connecting…")
        try:
            await connect_after_pairing(self.adb, host)
        except (WifiPairError, AdbError) as e:
            self.status.set_label(str(e))
            return
        self.pair_box.set_visible(False)
        await self._setup()

    async def _setup(self) -> None:
        try:
            await Pairing(self.adb, self.progress).run()
            self.done.set_visible(True)
        except PairError as e:
            self.progress("fail", str(e))
        except (AdbError, OSError, RuntimeError) as e:
            self.progress("fail", f"{e}. Close and open setup again: finished steps are skipped.")


def run() -> int:
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())
    app = Adw.Application(application_id="dev.tablink.Setup", flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect("activate", lambda a: Wizard(a).present())
    code: int = app.run([sys.argv[0]])
    return code
