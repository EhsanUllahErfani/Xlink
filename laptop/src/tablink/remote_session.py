"""F2 from the laptop's side: make remote desktop ready and ask TabLink to show it.

The RDP engine (FreeRDP) is built into the TabLink tablet app, which keeps the login
encrypted; the laptop only makes sure GNOME Remote Desktop is on and tunnelled, then asks
TabLink to open the session. Nothing is typed on the tablet.
"""

from __future__ import annotations

from tablink.adb import Adb

TABLINK = "dev.tablink"
OPEN_LAPTOP_EXTRA = "open_laptop"  # MainActivity.EXTRA_OPEN_LAPTOP


async def prepare_remote(adb: Adb, serial: str, rd: object | None = None) -> None:
    """Make sure RDP is up and tunnelled to the tablet's 127.0.0.1:3389."""
    from tablink.services.remote_desktop import RDP_PORT, RemoteDesktop

    remote = rd if isinstance(rd, RemoteDesktop) else RemoteDesktop()
    await remote.setup()
    await adb.reverse(serial, RDP_PORT, RDP_PORT)


async def show_laptop_on_tablet(adb: Adb, serial: str, rd: object | None = None) -> str:
    """Open the laptop desktop inside TabLink on the tablet. Returns a message for the user."""
    await prepare_remote(adb, serial, rd)
    # A locked tablet would hide the session: wake it and bring up the PIN pad first. The
    # session then appears as soon as the user unlocks.
    state = await adb.wake_and_show_unlock(serial)
    await adb.shell(
        serial, "am", "start", "-n", f"{TABLINK}/.MainActivity", "--ez", OPEN_LAPTOP_EXTRA, "true"
    )
    if state == "locked":
        return "Unlock the tablet: the laptop desktop opens right after."
    return "The laptop desktop is opening on the tablet."
