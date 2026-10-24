"""The setup window (T17): `tablink pair` with a face. Same steps, shown as they happen."""

from __future__ import annotations

import asyncio
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from tablink.adb import Adb, AdbError  # noqa: E402
from tablink.pairing import PairError, Pairing  # noqa: E402

ICONS = {
    "run": "content-loading-symbolic",
    "wait": "dialog-information-symbolic",
    "ok": "emblem-ok-symbolic",
    "skip": "emblem-ok-symbolic",
    "fail": "dialog-error-symbolic",
}

INTRO = (
    "1. On the tablet: Settings &gt; About tablet &gt; tap <b>OS version</b> 7 times.\n"
    "2. Settings &gt; Additional settings &gt; Developer options: turn on <b>USB debugging</b>, "
    "<b>USB debugging (Security settings)</b> and <b>Install via USB</b>.\n"
    "3. Plug the tablet into this laptop and tap <b>Allow</b> on the tablet."
)


class Wizard(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Set up TabLink")
        self.set_default_size(560, 620)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        intro = Gtk.Label(label=INTRO, use_markup=True, wrap=True, xalign=0)
        self.start = Gtk.Button(label="Start setup", css_classes=["suggested-action", "pill"])
        self.start.connect("clicked", lambda *_: self._run())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(24)
        box.append(intro)
        box.append(self.rows)
        box.append(self.start)
        scroll = Gtk.ScrolledWindow(child=box, vexpand=True)
        view = Adw.ToolbarView(content=scroll)
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        self._tasks: set[asyncio.Task[None]] = set()

    def progress(self, state: str, text: str) -> None:
        row = Adw.ActionRow(title=GLib.markup_escape_text(text), title_lines=4)
        row.add_prefix(Gtk.Image(icon_name=ICONS.get(state, "emblem-default-symbolic")))
        if state == "fail":
            row.add_css_class("error")
        self.rows.append(row)

    def _run(self) -> None:
        self.start.set_sensitive(False)
        self.start.set_label("Setting up…")
        task = asyncio.ensure_future(self._pair())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _pair(self) -> None:
        try:
            await Pairing(Adb(), self.progress).run()
            self.start.set_label("Done")
        except PairError as e:
            self.progress("fail", str(e))
            self._retry()
        except (AdbError, OSError, RuntimeError) as e:
            self.progress("fail", f"{e}. Try again: finished steps are skipped.")
            self._retry()

    def _retry(self) -> None:
        self.start.set_label("Try again")
        self.start.set_sensitive(True)


def run() -> int:
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())
    app = Adw.Application(application_id="dev.tablink.Setup", flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect("activate", lambda a: Wizard(a).present())
    return app.run([sys.argv[0]])
