"""Create aFreeRDP's saved "Laptop" connection, so the tablet never shows a login box.

Drives aFreeRDP's own Connection Settings screens through adb (tap + type), reading the screen
from the accessibility tree before every step. Every action first checks that aFreeRDP still
has focus; if the user switches apps, setup stops and nothing more is typed.

Verified against aFreeRDP 3.32 on Android 16 (HyperOS 3).
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tablink.adb import Adb
from tablink.remote_session import AFREERDP, Box, FocusLost, _box

log = logging.getLogger(__name__)

HOME = "com.freerdp.freerdpcore.presentation.HomeActivity"
LABEL = "Laptop"


class SetupFailed(RuntimeError):
    pass


@dataclass
class Node:
    text: str
    rid: str
    cls: str
    box: Box


def parse_nodes(xml: str) -> list[Node]:
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for n in root.iter("node"):
        if n.get("package") != AFREERDP:
            continue
        b = _box(n)
        if b is None:
            continue
        out.append(
            Node(
                text=n.get("text") or n.get("content-desc") or "",
                rid=(n.get("resource-id") or "").rsplit("/", 1)[-1],
                cls=(n.get("class") or "").rsplit(".", 1)[-1],
                box=b,
            )
        )
    return out


def find(nodes: list[Node], *, text: str | None = None, rid: str | None = None) -> Node | None:
    for n in nodes:
        if (text is None or n.text == text) and (rid is None or n.rid == rid):
            return n
    return None


def row_for(nodes: list[Node], title: str) -> Node | None:
    """The tappable preference row whose title is `title`."""
    return find(nodes, text=title, rid="title")


def existing_bookmark(nodes: list[Node], label: str = LABEL) -> bool:
    return find(nodes, text=label, rid="bookmark_text1") is not None


class AFreeRdpSetup:
    def __init__(self, adb: Adb, serial: str, step_wait_s: float = 1.2) -> None:
        self.adb = adb
        self.serial = serial
        self.wait = step_wait_s

    async def _screen(self) -> list[Node]:
        await asyncio.sleep(self.wait)
        return parse_nodes(await self.adb.ui_dump(self.serial))

    async def _guard(self) -> None:
        focus = await self.adb.focused_window(self.serial)
        if focus is None or focus[0] != AFREERDP:
            raise FocusLost("aFreeRDP left the screen; setup stopped")

    async def _tap(self, node: Node) -> None:
        await self._guard()
        await self.adb.tap(self.serial, *node.box.center)

    async def _need(self, what: str, **kw: str) -> Node:
        nodes = await self._screen()
        node = find(nodes, **kw)
        if node is None:
            raise SetupFailed(f"aFreeRDP screen changed: could not find {what}")
        return node

    async def _edit_text(self, row_title: str, value: str, secret: bool = False) -> None:
        nodes = await self._screen()
        row = row_for(nodes, row_title)
        if row is None:
            raise SetupFailed(f"no '{row_title}' setting")
        await self._tap(row)
        title = await self._need(f"the {row_title} dialog", rid="alertTitle")
        if title.text != row_title:
            raise SetupFailed(f"expected the {row_title} dialog, got '{title.text}'")
        await self._tap(await self._need("its text box", rid="edit"))
        await self._guard()
        if secret:
            await self.adb.type_secret(self.serial, value)
        else:
            await self.adb.shell(self.serial, "input", "text", value)
        await self._tap(await self._need("its OK button", rid="button1"))

    async def _choose(self, row_title: str, option: str) -> None:
        nodes = await self._screen()
        row = row_for(nodes, row_title)
        if row is None:
            raise SetupFailed(f"no '{row_title}' setting")
        await self._tap(row)
        await self._tap(await self._need(f"the '{option}' choice", text=option))

    async def run(self, host: str, port: int, user: str, password: str) -> bool:
        """Create the saved connection. Returns False if it already existed."""
        await self.adb.shell(self.serial, "am", "force-stop", AFREERDP)
        await self.adb.shell(self.serial, "am", "start", "-n", f"{AFREERDP}/{HOME}")
        nodes = await self._screen()
        await self._guard()
        if existing_bookmark(nodes):
            return False
        new = find(nodes, rid="newBookmark")
        if new is None:
            raise SetupFailed("aFreeRDP's 'New Connection' button is missing")
        await self._tap(new)
        await self._edit_text("Label", LABEL)
        await self._edit_text("Hostname", host)
        if port != 3389:
            await self._edit_text("Port", str(port))
        # Credentials live on a sub-screen.
        nodes = await self._screen()
        creds = row_for(nodes, "Credentials")
        if creds is None:
            raise SetupFailed("no Credentials setting")
        await self._tap(creds)
        await self._edit_text("Username", user)
        await self._edit_text("Password", password, secret=True)
        await self._tap(await self._need("the back arrow", text="Navigate up"))
        # Screen: let aFreeRDP fit the session to the tablet.
        nodes = await self._screen()
        screen = row_for(nodes, "Screen")
        if screen is not None:
            await self._tap(screen)
            await self._choose("Resolution", "Fit Screen")
            await self._tap(await self._need("the back arrow", text="Navigate up"))
        await self._tap(await self._need("the Save button", rid="action_save"))
        nodes = await self._screen()
        if not existing_bookmark(nodes):
            raise SetupFailed("the saved connection did not appear in aFreeRDP")
        return True
