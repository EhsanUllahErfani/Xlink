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
    assert await launcher.connect(notes, adb, svc) == launcher.OK
    assert notes.seen == []
    assert f"--serial={USB_SERIAL}" in sp.last.argv
    assert config.load().device.tablet_serial == USB_SERIAL
    assert fake.reverses[USB_SERIAL] == {}  # tunnel removed when the window closed


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
    assert await launcher.connect(notes, adb, svc) == launcher.NO_TABLET
    assert notes.seen[0][0] == "Tablet not found"
    assert hint in notes.seen[0][1]
    assert sp.procs == []


async def test_window_crash_is_reported() -> None:
    adb, sp, svc = parts(FakeAdb())
    sp.exit_immediately = (2, ("ERROR: Device disconnected",))
    notes = Notes()
    assert await launcher.connect(notes, adb, svc) == launcher.NO_TABLET
    assert notes.seen == [("Tablet window closed", "The tablet disconnected.")]


def test_single_instance_lock() -> None:
    with launcher.single_instance("t") as first:
        assert first
        with launcher.single_instance("t") as second:
            assert not second
    with launcher.single_instance("t") as again:
        assert again
