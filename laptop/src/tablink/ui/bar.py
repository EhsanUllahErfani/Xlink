"""The TabLink bar: a small window next to the tablet screen with the buttons a mouse lacks.

HyperOS navigates with swipe gestures, which are awkward with a mouse, so the bar gives Back,
Home, Recent apps and Close app as plain buttons, plus Send files (button or drag and drop).
`tablink connect` starts it with the session and closes it when the session ends.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from tablink.adb import Adb, AdbError  # noqa: E402
from tablink.services.scrcpy import TABLET_DROP_DIR  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "dev.tablink.Bar"
EXIT_DISCONNECT = 10

# Never force-stop these: closing them would blank the tablet or break the session.
PROTECTED = {
    "com.miui.home",
    "com.android.systemui",
    "com.android.launcher3",
    "com.google.android.apps.nexuslauncher",
    "com.genymobile.scrcpy",
}

HELP_TEXT = (
    "<b>Mouse in the tablet window</b>\n"
    "Right-click  →  Back\n"
    "Middle-click  →  Home\n"
    "Drag a file onto it  →  send to tablet\n\n"
    "<b>Keys in the tablet window</b>\n"
    "Alt+B  Back      Alt+H  Home\n"
    "Alt+S  Recent apps\n"
    "Alt+F  Full screen      Alt+R  Rotate\n"
    "Alt+O  Tablet screen off (saves battery)\n"
    "Ctrl+C / Ctrl+V  copy and paste between devices"
)


class BarWindow(Adw.ApplicationWindow):
    def __init__(self, app: BarApp) -> None:
        super().__init__(application=app, title="TabLink")
        self.app = app
        self.set_default_size(560, -1)
        self.set_resizable(False)

        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=app.tablet_name, subtitle=app.subtitle)
        header.set_title_widget(self.title)

        help_btn = Gtk.MenuButton(icon_name="help-about-symbolic", tooltip_text="Shortcuts")
        label = Gtk.Label(label=HELP_TEXT, use_markup=True, margin_top=12, margin_bottom=12)
        label.set_margin_start(12)
        label.set_margin_end(12)
        pop = Gtk.Popover(child=label)
        help_btn.set_popover(pop)
        header.pack_end(help_btn)

        disconnect = Gtk.Button(icon_name="network-offline-symbolic", tooltip_text="Disconnect")
        disconnect.connect("clicked", lambda *_: app.disconnect())
        header.pack_start(disconnect)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, homogeneous=True)
        row.set_margin_top(6)
        row.set_margin_bottom(12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        buttons: list[tuple[str, str, str, Callable[[], Awaitable[None]]]] = [
            ("go-previous-symbolic", "Back", "Back (right-click)", app.back),
            ("go-home-symbolic", "Home", "Home (middle-click)", app.home),
            ("view-app-grid-symbolic", "Recent", "Recent apps (Alt+S)", app.recents),
            ("window-close-symbolic", "Close app", "Close the app on screen", app.close_app),
            ("document-send-symbolic", "Send files", "Send files to the tablet", self.pick),
        ]
        for icon, text, tip, action in buttons:
            btn = Gtk.Button(tooltip_text=tip)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.append(Gtk.Image(icon_name=icon, pixel_size=22))
            box.append(Gtk.Label(label=text, css_classes=["caption"]))
            btn.set_child(box)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b, a=action: app.spawn(a()))
            row.append(btn)

        self.toasts = Adw.ToastOverlay(child=row)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def toast(self, text: str, timeout: int = 3) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def _on_drop(self, _target: Gtk.DropTarget, value: Gdk.FileList, _x: float, _y: float) -> bool:
        paths = [Path(f.get_path()) for f in value.get_files() if f.get_path()]
        self.app.spawn(self.app.send(paths))
        return True

    async def pick(self) -> None:
        dialog = Gtk.FileDialog(title="Send files to the tablet", modal=True)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[Path]] = loop.create_future()

        def done(d: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                files = d.open_multiple_finish(res)
                fut.set_result([Path(f.get_path()) for f in files if f.get_path()])
            except GLib.Error:  # cancelled
                fut.set_result([])

        dialog.open_multiple(self, None, done)
        await self.app.send(await fut)


class BarApp(Adw.Application):
    def __init__(self, adb: Adb, serial: str, tablet_name: str, subtitle: str) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.adb = adb
        self.serial = serial
        self.tablet_name = tablet_name
        self.subtitle = subtitle
        self.win: BarWindow | None = None
        self.exit_code = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self.connect("activate", self._on_activate)

    def _on_activate(self, _app: Adw.Application) -> None:
        if self.win is None:
            self.win = BarWindow(self)
        self.win.present()

    def spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def toast(self, text: str, timeout: int = 3) -> None:
        if self.win:
            self.win.toast(text, timeout)

    async def _key(self, key: str) -> None:
        try:
            await self.adb.keyevent(self.serial, key)
        except AdbError as e:
            self.toast(f"Tablet did not respond: {e}")

    async def back(self) -> None:
        await self._key("KEYCODE_BACK")

    async def home(self) -> None:
        await self._key("KEYCODE_HOME")

    async def recents(self) -> None:
        await self._key("KEYCODE_APP_SWITCH")

    async def close_app(self) -> None:
        try:
            pkg = await self.adb.foreground_package(self.serial)
            if pkg is None or pkg in PROTECTED:
                self.toast("No app is open")
                return
            await self.adb.force_stop(self.serial, pkg)
            await self.adb.keyevent(self.serial, "KEYCODE_HOME")
        except AdbError as e:
            self.toast(f"Could not close the app: {e}")
            return
        self.toast(f"Closed {pkg}")

    async def send(self, paths: list[Path]) -> None:
        files = [p for p in paths if p.is_file()]
        if not files:
            if paths:
                self.toast("Only files can be sent (not folders)")
            return
        for i, f in enumerate(files, 1):
            what = f.name if len(files) == 1 else f"{i} of {len(files)}: {f.name}"
            self.toast(f"Sending {what}…", timeout=0 if f.stat().st_size > 50e6 else 3)
            try:
                await self.adb.push(self.serial, f, TABLET_DROP_DIR)
            except AdbError as e:
                self.toast(f"Could not send {f.name}: {e}", timeout=6)
                return
        n = "1 file" if len(files) == 1 else f"{len(files)} files"
        self.toast(f"Sent {n} to Download/TabLink on the tablet", timeout=5)

    def disconnect(self) -> None:
        self.exit_code = EXIT_DISCONNECT
        self.quit()


def run(serial: str, tablet_name: str, subtitle: str, adb: Adb | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())
    app = BarApp(adb or Adb(), serial, tablet_name, subtitle)
    code = app.run([sys.argv[0]])
    return app.exit_code or code
