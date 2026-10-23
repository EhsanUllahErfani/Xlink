"""Control bar actions. Needs PyGObject (the `ui` extra); skipped where it is not installed."""

from pathlib import Path

import pytest

pytest.importorskip("gi")
bar = pytest.importorskip("tablink.ui.bar")

from tablink.adb import Adb  # noqa: E402
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb  # noqa: E402


class App(bar.BarApp):  # type: ignore[misc,name-defined]
    """BarApp with toasts captured instead of shown."""

    def __init__(self, fake: FakeAdb) -> None:
        super().__init__(Adb(runner=fake, adb_path="adb"), USB_SERIAL, "Pad", "USB")
        self.toasts: list[str] = []

    def toast(self, text: str, timeout: int = 3) -> None:
        self.toasts.append(text)


async def test_navigation_buttons_send_keys() -> None:
    fake = FakeAdb()
    app = App(fake)
    await app.back()
    await app.home()
    await app.recents()
    keys = [c[-1] for c in fake.calls if "keyevent" in c]
    assert keys == ["KEYCODE_BACK", "KEYCODE_HOME", "KEYCODE_APP_SWITCH"]


async def test_close_app_stops_foreground_app_and_goes_home() -> None:
    fake = FakeAdb(foreground="com.facebook.katana")
    app = App(fake)
    await app.close_app()
    assert fake.stopped == ["com.facebook.katana"]
    assert fake.calls[-1][-1] == "KEYCODE_HOME"
    assert app.toasts == ["Closed com.facebook.katana"]


async def test_close_app_never_stops_launcher() -> None:
    fake = FakeAdb(foreground="com.miui.home")
    app = App(fake)
    await app.close_app()
    assert fake.stopped == [] and app.toasts == ["No app is open"]


async def test_send_files_pushes_each_and_reports(tmp_path: Path) -> None:
    fake = FakeAdb()
    app = App(fake)
    a, b = tmp_path / "a.txt", tmp_path / "b.pdf"
    a.write_text("a")
    b.write_text("b")
    await app.send([a, b, tmp_path])  # folders are skipped
    assert [p for p, _ in fake.pushed] == [str(a), str(b)]
    assert app.toasts[-1] == "Sent 2 files to Download/TabLink on the tablet"


async def test_send_reports_failure(tmp_path: Path) -> None:
    fake = FakeAdb(usb_attached=False)
    app = App(fake)
    f = tmp_path / "a.txt"
    f.write_text("a")
    await app.send([f])
    assert app.toasts[-1].startswith("Could not send a.txt")


async def test_wake_button_tells_user_to_type_pin() -> None:
    fake = FakeAdb(awake=False, locked=True)
    app = App(fake)
    await app.wake()
    assert fake.awake
    assert "Type your PIN" in app.toasts[-1]


def test_middle_dot_rule_matches_android() -> None:
    from tablink.ui.unlock_pad import middle_dot

    assert middle_dot(1, 3) == 2
    assert middle_dot(1, 9) == 5
    assert middle_dot(3, 7) == 5
    assert middle_dot(1, 7) == 4
    assert middle_dot(1, 6) is None  # knight move: no dot in between
    assert middle_dot(1, 2) is None


class FakePad:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeWin:
    def __init__(self) -> None:
        self.pad: FakePad | None = None
        self.pad_dismissed = False
        self.opened = 0

    async def unlock(self) -> None:
        self.opened += 1
        self.pad = FakePad()


async def run_watch(app: "App", rounds: int) -> None:
    import asyncio

    task = asyncio.create_task(app.watch_lock(interval_s=0))
    for _ in range(rounds):
        await asyncio.sleep(0)
    task.cancel()


async def test_pad_opens_by_itself_when_locked_and_closes_when_unlocked() -> None:
    fake = FakeAdb(awake=True, locked=True)
    app = App(fake)
    win = FakeWin()
    app.win = win
    await run_watch(app, 20)
    assert win.opened == 1 and win.pad is not None  # opened once, not again and again
    pad = win.pad
    fake.locked = False
    await run_watch(app, 20)
    assert win.pad is None and pad.closed


async def test_pad_closed_by_user_stays_closed_until_next_lock() -> None:
    fake = FakeAdb(awake=True, locked=True)
    app = App(fake)
    win = FakeWin()
    win.pad_dismissed = True  # the user closed it
    app.win = win
    await run_watch(app, 20)
    assert win.opened == 0
    fake.locked = False
    await run_watch(app, 20)
    fake.locked = True
    await run_watch(app, 20)
    assert win.opened == 1  # next lock: opens again
