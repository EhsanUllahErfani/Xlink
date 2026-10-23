"""F2 on the tablet: open the laptop's desktop in aFreeRDP and sign in.

Typing the password is guarded: before every tap and every keystroke we confirm that
aFreeRDP's session window still has focus. If the user switched apps, we stop, so the
password can never land in another app.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tablink.adb import Adb, AdbError

log = logging.getLogger(__name__)

AFREERDP = "com.freerdp.afreerdp"
SESSION_ACTIVITY = "com.freerdp.freerdpcore.presentation.SessionActivity"


class FocusLost(RuntimeError):
    """The aFreeRDP window lost focus; nothing more was typed."""


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _box(node: ET.Element) -> Box | None:
    m = _BOUNDS.fullmatch(node.get("bounds", ""))
    return Box(*map(int, m.groups())) if m else None


@dataclass
class LoginDialog:
    password_field: Box
    ok_button: Box


def find_login_dialog(xml: str) -> LoginDialog | None:
    """Find aFreeRDP's credentials dialog in a uiautomator dump."""
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    nodes = [n for n in root.iter("node") if n.get("package") == AFREERDP]
    edits = [n for n in nodes if n.get("class", "").endswith("EditText")]
    password = next((n for n in edits if n.get("password") == "true"), None)
    if password is None and len(edits) >= 2:
        password = edits[1]  # username, password, domain
    ok = next(
        (
            n
            for n in nodes
            if n.get("class", "").endswith("Button") and n.get("text", "").upper() == "OK"
        ),
        None,
    )
    if password is None or ok is None:
        return None
    pb, ob = _box(password), _box(ok)
    return LoginDialog(pb, ob) if pb and ob else None


def session_link(host: str, port: int, user: str) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"freerdp://{user}@{h}:{port}"


async def _require_focus(adb: Adb, serial: str) -> None:
    focus = await adb.focused_window(serial)
    if focus is None or focus[0] != AFREERDP:
        raise FocusLost(f"aFreeRDP is not in front (focus: {focus[0] if focus else 'none'})")


async def open_laptop(
    adb: Adb,
    serial: str,
    host: str,
    port: int,
    user: str,
    password: str,
    wait_s: float = 15.0,
    poll_s: float = 0.5,
) -> str:
    """Open the laptop desktop on the tablet. Returns "signed-in" or "already-open"."""
    await adb.shell(
        serial,
        "am", "start", "-a", "android.intent.action.VIEW",
        "-d", session_link(host, port, user),
        "-n", f"{AFREERDP}/{SESSION_ACTIVITY}",
    )  # fmt: skip
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        await asyncio.sleep(poll_s)
        try:
            focus = await adb.focused_window(serial)
            if focus is None or focus[0] != AFREERDP:
                continue
            dialog = find_login_dialog(await adb.ui_dump(serial))
        except AdbError as e:
            log.debug("waiting for aFreeRDP: %s", e)
            continue
        if dialog is None:
            continue
        await _require_focus(adb, serial)
        await adb.tap(serial, *dialog.password_field.center)
        await _require_focus(adb, serial)
        await adb.type_secret(serial, password)
        await _require_focus(adb, serial)
        await adb.tap(serial, *dialog.ok_button.center)
        return "signed-in"
    focus = await adb.focused_window(serial)
    if focus and focus[0] == AFREERDP:
        return "already-open"
    raise FocusLost("aFreeRDP did not come to the front")


async def show_laptop_on_tablet(adb: Adb, serial: str, rd: object | None = None) -> str:
    """Make sure RDP is up, tunnel it over USB, and sign the tablet in. Returns a message."""
    from tablink.services.remote_desktop import RDP_PORT, RDP_USER, RemoteDesktop

    remote = rd if isinstance(rd, RemoteDesktop) else RemoteDesktop()
    await remote.setup()
    password = await remote.password()
    if password is None:
        return "Remote desktop has no password yet. Run `tablink remote` again."
    await adb.reverse(serial, RDP_PORT, RDP_PORT)
    try:
        result = await open_laptop(adb, serial, "127.0.0.1", RDP_PORT, RDP_USER, password)
    except FocusLost:
        return "Stopped: another app came to the front on the tablet. Nothing was typed."
    if result == "signed-in":
        return "The laptop desktop is opening on the tablet."
    return "aFreeRDP is open on the tablet."
