from tablink.adb import Adb
from tablink.remote_session import show_laptop_on_tablet
from tablink.services.remote_desktop import RemoteDesktop
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb
from tests.fakes.fake_grdctl import FakeGrd


async def test_show_laptop_prepares_rdp_and_asks_tablink(tmp_path: object) -> None:
    from pathlib import Path

    fake, grd = FakeAdb(), FakeGrd()
    rd = RemoteDesktop(runner=grd, tty=grd.tty, cert_dir=Path(str(tmp_path)) / "grd")
    msg = await show_laptop_on_tablet(Adb(runner=fake, adb_path="adb"), USB_SERIAL, rd)
    assert "opening" in msg and grd.enabled
    assert fake.reverses[USB_SERIAL][3389] == 3389
    assert fake.calls[-1][-4:] == ["dev.tablink/.MainActivity", "--ez", "open_laptop", "true"]
    assert not any("text" in c for c in fake.calls)  # nothing typed on the tablet


async def test_locked_tablet_is_woken_and_user_told_to_unlock(tmp_path: object) -> None:
    from pathlib import Path

    fake, grd = FakeAdb(awake=False, locked=True), FakeGrd()
    rd = RemoteDesktop(runner=grd, tty=grd.tty, cert_dir=Path(str(tmp_path)) / "grd")
    msg = await show_laptop_on_tablet(Adb(runner=fake, adb_path="adb"), USB_SERIAL, rd)
    assert fake.awake and msg.startswith("Unlock the tablet")
