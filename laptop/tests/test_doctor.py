import pytest

from tablink import doctor as doctor_mod
from tablink.adb import Adb
from tablink.doctor import Check, Doctor
from tests.fakes.fake_adb import FakeAdb


async def test_usb_states_give_clear_fixes() -> None:
    d = Doctor(Adb(runner=FakeAdb(authorized=False), adb_path="adb"))
    c = await d.usb()
    assert c.ok is False and "Allow" in c.fix
    c = await Doctor(Adb(runner=FakeAdb(usb_attached=False), adb_path="adb")).usb()
    assert c.ok is None  # unplugged is fine
    assert (await Doctor(Adb(runner=FakeAdb(), adb_path="adb")).usb()).ok is True


async def test_wifi_without_known_address_says_plug_in_once() -> None:
    c = await Doctor(Adb(runner=FakeAdb(), adb_path="adb")).wifi()
    assert c.ok is False and "USB" in c.fix


async def test_a_crashing_check_does_not_hide_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Doctor(Adb(runner=FakeAdb(), adb_path="adb"))

    async def boom() -> Check:
        raise RuntimeError("keyring locked")

    async def fine() -> Check:
        return Check("x", True, "ok")

    for name in ("service", "wifi", "pairing", "remote_desktop", "clipboard", "screen_tool",
                 "internet"):  # fmt: skip
        monkeypatch.setattr(d, name, fine)
    monkeypatch.setattr(d, "pairing", boom)
    checks = await d.run()
    assert len(checks) == 8
    bad = [c for c in checks if c.ok is False]
    assert len(bad) == 1 and "keyring locked" in bad[0].detail
    assert doctor_mod  # module imported for monkeypatching clarity
