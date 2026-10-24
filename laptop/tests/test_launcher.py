import pytest

from tablink import config, launcher
from tablink.adb import Adb
from tablink.services.scrcpy import ScrcpyService
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb
from tests.fakes.fake_scrcpy import FakeSpawner


class Notes:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str = "", urgent: bool = False) -> None:
        self.seen.append((title, body))


def parts(fake: FakeAdb) -> tuple[Adb, FakeSpawner, ScrcpyService]:
    adb = Adb(runner=fake, adb_path="adb")
    sp = FakeSpawner(exit_immediately=(0, ()))  # the user closes the window at once
    return adb, sp, ScrcpyService(adb, config.Config().video, "T", sp, scrcpy_path="scrcpy")


async def test_one_click_opens_tablet_and_remembers_it() -> None:
    fake = FakeAdb()
    adb, sp, svc = parts(fake)
    notes = Notes()
    bars = FakeSpawner()
    assert await launcher.connect(notes, adb, svc, spawn=bars) == launcher.OK
    assert notes.seen == []
    assert f"--serial={USB_SERIAL}" in bars.last.argv and "bar" in bars.last.argv
    assert bars.last.terminated  # bar closed with the window
    assert f"--serial={USB_SERIAL}" in sp.last.argv
    assert config.load().device.tablet_serial == USB_SERIAL
    assert fake.reverses.get(USB_SERIAL, {}) == {}  # no adb tunnel in the new design


@pytest.mark.parametrize(
    ("fake", "hint"),
    [
        (FakeAdb(usb_attached=False), "USB-C cable"),
        (FakeAdb(authorized=False), "Allow USB debugging"),
    ],
)
async def test_no_tablet_explains_what_to_do(fake: FakeAdb, hint: str) -> None:
    adb, sp, svc = parts(fake)
    notes = Notes()
    assert await launcher.connect(notes, adb, svc, spawn=FakeSpawner()) == launcher.NO_TABLET
    assert notes.seen[0][0] == "Tablet not found"
    assert hint in notes.seen[0][1]
    assert sp.procs == []


async def test_window_crash_is_reported() -> None:
    adb, sp, svc = parts(FakeAdb())
    sp.exit_immediately = (2, ("ERROR: Device disconnected",))
    notes = Notes()
    assert await launcher.connect(notes, adb, svc, spawn=FakeSpawner()) == launcher.NO_TABLET
    assert notes.seen == [("Tablet window closed", "The tablet disconnected.")]


def test_single_instance_lock() -> None:
    with launcher.single_instance("t") as first:
        assert first
        with launcher.single_instance("t") as second:
            assert not second
    with launcher.single_instance("t") as again:
        assert again


async def test_bar_disconnect_button_closes_window() -> None:
    adb, sp, svc = parts(FakeAdb())
    sp.exit_immediately = None  # window stays open
    bars = FakeSpawner(exit_immediately=(launcher.BAR_EXIT_DISCONNECT, ()))
    assert await launcher.connect(Notes(), adb, svc, spawn=bars) == launcher.OK
    assert sp.last.terminated


async def test_closing_only_the_bar_keeps_window() -> None:
    import asyncio

    adb, sp, svc = parts(FakeAdb())
    sp.exit_immediately = None
    bars = FakeSpawner(exit_immediately=(0, ()))
    task = asyncio.create_task(launcher.connect(Notes(), adb, svc, spawn=bars))
    for _ in range(20):
        await asyncio.sleep(0)
    assert not task.done() and not sp.last.terminated
    sp.last.exit(0)  # now the user closes the window
    assert await task == launcher.OK


async def test_bar_that_cannot_start_does_not_block_session() -> None:
    adb, _sp, svc = parts(FakeAdb())

    async def broken(argv: object, env: object) -> object:
        raise OSError("no python")

    assert await launcher.connect(Notes(), adb, svc, spawn=broken) == launcher.OK  # type: ignore[arg-type]
