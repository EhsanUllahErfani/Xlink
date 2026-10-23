import asyncio

from tablink import config
from tablink.adb import Adb
from tablink.launcher import BAR_EXIT_DISCONNECT
from tablink.paths.base import Path
from tablink.screen import ScreenSession
from tablink.services.scrcpy import ScrcpyService
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb
from tests.fakes.fake_scrcpy import FakeSpawner

USB = Path("usb", "127.0.0.1", "127.0.0.1", USB_SERIAL, 2.0, True)
LAN = Path("lan", "192.0.2.20", "192.0.2.5", "192.0.2.20:5555", 9.0, True)


class Notes:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str = "", urgent: bool = False) -> None:
        self.seen.append((title, body))


def make() -> tuple[ScreenSession, FakeSpawner, FakeSpawner, Notes]:
    adb = Adb(runner=FakeAdb(connected_tcp=["192.0.2.20:5555"]), adb_path="adb")
    windows, bars = FakeSpawner(), FakeSpawner()
    svc = ScrcpyService(adb, config.Config().video, "T", windows, scrcpy_path="scrcpy")
    notes = Notes()
    return ScreenSession(svc, bars, config.Config(), notes), windows, bars, notes


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_open_without_path_explains() -> None:
    s, windows, _, _ = make()
    assert "Tablet not found" in await s.open()
    assert windows.procs == []


async def test_cable_pull_moves_window_and_bar_to_wifi() -> None:
    s, windows, bars, _ = make()
    await s.on_path_changed(USB)
    assert (await s.open()).startswith("Opening")
    assert f"--serial={USB_SERIAL}" in windows.last.argv and len(bars.live) == 1
    await s.on_path_changed(LAN)  # cable pulled, Wi-Fi path took over
    assert "--serial=192.0.2.20:5555" in windows.last.argv
    assert "--video-bit-rate=16M" in windows.last.argv  # LAN keeps the High preset
    assert len(windows.live) == 1 and len(bars.live) == 1
    assert "--serial=192.0.2.20:5555" in bars.last.argv


async def test_second_open_does_not_duplicate() -> None:
    s, windows, bars, _ = make()
    await s.on_path_changed(USB)
    await s.open()
    assert "already open" in await s.open()
    assert len(windows.procs) == 1 and len(bars.procs) == 1


async def test_bar_disconnect_closes_window() -> None:
    s, windows, bars, _ = make()
    await s.on_path_changed(USB)
    await s.open()
    bars.last.exit(BAR_EXIT_DISCONNECT)
    await settle()
    assert windows.live == [] and not s.scrcpy.wanted


async def test_window_error_is_notified_and_bar_closed() -> None:
    s, windows, bars, notes = make()
    await s.on_path_changed(USB)
    await s.open()
    windows.last.exit(1, "ERROR: Device is unauthorized")
    await settle()
    assert bars.live == []
    assert notes.seen and "Unlock the tablet" in notes.seen[0][1]
