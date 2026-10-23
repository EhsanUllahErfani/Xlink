"""Unlock the tablet from the laptop: draw the user's pattern or type their PIN.

HyperOS hides the lock screen from screen capture, so the tablet window shows what is under
it. The laptop's unlock pad collects the pattern (or PIN) and this module replays it on the
lock screen. The secret is used once and never stored or logged: the adb command that draws
it is run with its arguments hidden from the log.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass

from tablink.adb import Adb

SYSTEMUI = "com.android.systemui"


class UnlockError(RuntimeError):
    pass


@dataclass(frozen=True)
class Grid:
    left: int
    top: int
    right: int
    bottom: int

    def dot(self, n: int) -> tuple[int, int]:
        """Centre of dot n (1..9, rows left to right, top to bottom)."""
        row, col = divmod(n - 1, 3)
        w, h = self.right - self.left, self.bottom - self.top
        return self.left + int(w * (col + 0.5) / 3), self.top + int(h * (row + 0.5) / 3)


@dataclass(frozen=True)
class LockScreen:
    kind: str  # "pattern", "pin", "password", "none"
    grid: Grid | None = None


def parse_lock_screen(xml: str) -> LockScreen:
    if not xml:
        return LockScreen("none")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return LockScreen("none")
    ids: dict[str, ET.Element] = {}
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").removeprefix(f"{SYSTEMUI}:id/")
        if rid:
            ids.setdefault(rid, n)
    if "lockPatternView" in ids:
        m = re.findall(r"\d+", ids["lockPatternView"].get("bounds", ""))
        if len(m) == 4:
            return LockScreen("pattern", Grid(*map(int, m)))
    if "pinEntry" in ids or "keyguard_pin_view" in ids:
        return LockScreen("pin")
    if "passwordEntry" in ids or "keyguard_password_view" in ids:
        return LockScreen("password")
    return LockScreen("none")


def valid_pattern(dots: Sequence[int]) -> bool:
    return 4 <= len(dots) <= 9 and len(set(dots)) == len(dots) and all(1 <= d <= 9 for d in dots)


def pattern_script(grid: Grid, dots: Sequence[int]) -> str:
    """One shell line that draws the pattern as a single finger stroke."""
    points = [grid.dot(d) for d in dots]
    steps = [f"input motionevent DOWN {points[0][0]} {points[0][1]}"]
    steps += [f"input motionevent MOVE {x} {y}" for x, y in points[1:]]
    steps.append(f"input motionevent UP {points[-1][0]} {points[-1][1]}")
    return "; ".join(steps)


async def show_lock_screen(adb: Adb, serial: str, wait_s: float = 6.0) -> LockScreen:
    """Wake the tablet, bring up the pattern/PIN screen and read what kind it is."""
    if await adb.wake_and_show_unlock(serial) == "unlocked":
        return LockScreen("none")
    loop = asyncio.get_running_loop()
    end = loop.time() + wait_s
    while loop.time() < end:
        screen = parse_lock_screen(await adb.ui_dump(serial))
        if screen.kind != "none":
            return screen
        await asyncio.sleep(0.5)
    _, locked = await adb.screen_state(serial)
    return LockScreen("none") if not locked else LockScreen("unknown")


async def unlock_with_pattern(adb: Adb, serial: str, dots: Sequence[int]) -> bool:
    """Draw `dots` on the lock screen. Returns True if the tablet is unlocked afterwards."""
    if not valid_pattern(dots):
        raise UnlockError("a pattern joins 4 to 9 different dots")
    screen = await show_lock_screen(adb, serial)
    if screen.kind == "none":
        return True  # already unlocked
    if screen.kind != "pattern" or screen.grid is None:
        raise UnlockError("the tablet is not asking for a pattern")
    await adb.shell_secret(serial, pattern_script(screen.grid, dots))
    await asyncio.sleep(1.0)
    return not (await adb.screen_state(serial))[1]


async def unlock_with_pin(adb: Adb, serial: str, pin: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9]{4,32}", pin):
        raise UnlockError("a PIN or password of 4 to 32 letters or digits")
    screen = await show_lock_screen(adb, serial)
    if screen.kind == "none":
        return True
    if screen.kind not in ("pin", "password"):
        raise UnlockError("the tablet is not asking for a PIN")
    await adb.type_secret(serial, pin)
    await adb.keyevent(serial, "KEYCODE_ENTER")
    await asyncio.sleep(1.0)
    return not (await adb.screen_state(serial))[1]
