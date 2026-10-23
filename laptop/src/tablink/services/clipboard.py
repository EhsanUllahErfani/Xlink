"""F3 laptop side: share the GNOME clipboard with the tablet.

On Wayland only GNOME Shell may read the clipboard in the background, so this talks to the
TabLink shell extension (data/gnome-shell-extension) over D-Bus. Text is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

log = logging.getLogger(__name__)

MAX_TEXT = 512 * 1024


class Bridge(Protocol):
    def get_text(self) -> str | None: ...

    def set_text(self, text: str) -> None: ...


class ShellBridge:
    """D-Bus client for dev.tablink.Clipboard (blocking; call from a thread)."""

    NAME, PATH, IFACE = "dev.tablink.Shell", "/dev/tablink/Clipboard", "dev.tablink.Clipboard"

    def _call(self, method: str, args: object = None) -> object:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        params = GLib.Variant("(s)", (args,)) if args is not None else None
        reply = bus.call_sync(
            self.NAME, self.PATH, self.IFACE, method, params, None,
            Gio.DBusCallFlags.NONE, 2000, None,
        )  # fmt: skip
        return reply.unpack()[0] if reply is not None and reply.n_children() else None

    def get_text(self) -> str | None:
        try:
            value = self._call("GetText")
        except Exception as e:  # extension not loaded yet (needs one logout/login)
            log.debug("clipboard bridge unavailable: %s", e)
            return None
        return value if isinstance(value, str) else None

    def set_text(self, text: str) -> None:
        self._call("SetText", text)


class ClipboardSync:
    """Polls the laptop clipboard and pushes changes; applies text from the tablet."""

    name = "clipboard"
    needs_video = False

    def __init__(
        self,
        bridge: Bridge,
        push: Callable[[str], Awaitable[object]],
        interval_s: float = 1.0,
    ) -> None:
        self.bridge = bridge
        self.push = push
        self.interval_s = interval_s
        self._last: str | None = None
        self._task: asyncio.Task[None] | None = None
        self.available = False

    async def on_path_changed(self, path: object) -> None:  # clipboard works on any path
        return None

    async def from_tablet(self, text: str) -> None:
        text = text[:MAX_TEXT]
        self._last = text  # don't echo it back
        await asyncio.to_thread(self.bridge.set_text, text)

    async def check(self) -> None:
        text = await asyncio.to_thread(self.bridge.get_text)
        self.available = text is not None
        if text is None:
            return
        if self._last is None:
            self._last = text  # don't push whatever was copied before TabLink started
            return
        if text != self._last and text:
            self._last = text
            await self.push(text[:MAX_TEXT])

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("clipboard check failed")
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
