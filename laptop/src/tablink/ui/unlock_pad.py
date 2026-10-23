"""The unlock pad: draw the tablet's pattern (or type its PIN) on the laptop.

The pattern lives only in this window until it is sent; it is cleared right after, and never
stored or logged.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

SIZE = 300
HIT = 0.3  # how close (in cells) the pointer must come to a dot


def middle_dot(a: int, b: int) -> int | None:
    """The dot between a and b on a straight line (Android adds it automatically)."""
    ra, ca = divmod(a - 1, 3)
    rb, cb = divmod(b - 1, 3)
    if (ra + rb) % 2 or (ca + cb) % 2:
        return None
    mid = ((ra + rb) // 2) * 3 + (ca + cb) // 2 + 1
    return mid if mid not in (a, b) else None


class PatternPad(Gtk.DrawingArea):
    def __init__(self, on_done: Callable[[list[int]], None]) -> None:
        super().__init__(content_width=SIZE, content_height=SIZE)
        self.on_done = on_done
        self.dots: list[int] = []
        self.pointer: tuple[float, float] | None = None
        self._start = (0.0, 0.0)
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)

    def _center(self, n: int) -> tuple[float, float]:
        row, col = divmod(n - 1, 3)
        cell = SIZE / 3
        return (col + 0.5) * cell, (row + 0.5) * cell

    def _hit(self, x: float, y: float) -> None:
        cell = SIZE / 3
        for n in range(1, 10):
            cx, cy = self._center(n)
            if n not in self.dots and math.hypot(x - cx, y - cy) < HIT * cell:
                if self.dots:
                    mid = middle_dot(self.dots[-1], n)
                    if mid is not None and mid not in self.dots:
                        self.dots.append(mid)
                self.dots.append(n)
                return

    def _begin(self, _g: Gtk.GestureDrag, x: float, y: float) -> None:
        self.dots = []
        self._start = (x, y)
        self._hit(x, y)
        self.pointer = (x, y)
        self.queue_draw()

    def _update(self, _g: Gtk.GestureDrag, dx: float, dy: float) -> None:
        x, y = self._start[0] + dx, self._start[1] + dy
        self._hit(x, y)
        self.pointer = (x, y)
        self.queue_draw()

    def _end(self, _g: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        dots, self.dots, self.pointer = self.dots, [], None
        self.queue_draw()
        if dots:
            self.on_done(dots)

    def _draw(self, _area: Gtk.DrawingArea, cr: object, w: int, h: int) -> None:
        cr.set_source_rgba(0.12, 0.35, 0.66, 1)  # type: ignore[attr-defined]
        cr.set_line_width(6)  # type: ignore[attr-defined]
        if self.dots:
            cr.move_to(*self._center(self.dots[0]))  # type: ignore[attr-defined]
            for n in self.dots[1:]:
                cr.line_to(*self._center(n))  # type: ignore[attr-defined]
            if self.pointer:
                cr.line_to(*self.pointer)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
        for n in range(1, 10):
            cx, cy = self._center(n)
            cr.arc(cx, cy, 14 if n in self.dots else 9, 0, 2 * math.pi)  # type: ignore[attr-defined]
            if n in self.dots:
                cr.set_source_rgba(0.12, 0.35, 0.66, 1)  # type: ignore[attr-defined]
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 1)  # type: ignore[attr-defined]
            cr.fill()  # type: ignore[attr-defined]


class UnlockWindow(Adw.Window):
    """Draw the pattern or type the PIN; `send_pattern` / `send_pin` do the unlocking."""

    def __init__(
        self,
        parent: Gtk.Window,
        send_pattern: Callable[[list[int]], None],
        send_pin: Callable[[str], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title="Unlock the tablet")
        self.set_default_size(360, -1)
        self.status = Gtk.Label(
            label="Draw your pattern with the mouse",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        pad = PatternPad(send_pattern)
        pin = Gtk.PasswordEntry(show_peek_icon=False, placeholder_text="…or type your PIN")

        def on_pin(entry: Gtk.PasswordEntry) -> None:
            text = entry.get_text()
            entry.set_text("")  # never kept in the window
            send_pin(text)

        pin.connect("activate", on_pin)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(18)
        box.append(self.status)
        frame = Gtk.Frame(child=pad, halign=Gtk.Align.CENTER)
        box.append(frame)
        box.append(pin)
        note = Gtk.Label(
            label="Used once to unlock, never stored.", css_classes=["dim-label", "caption"]
        )
        box.append(note)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)

    def say(self, text: str) -> None:
        self.status.set_label(text)
