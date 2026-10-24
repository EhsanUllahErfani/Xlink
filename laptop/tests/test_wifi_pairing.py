import pytest

from tablink.adb import Adb
from tablink.wifi_pairing import (
    WifiPairError,
    connect_after_pairing,
    new_qr,
    pair_with_code,
    wait_and_pair_qr,
)
from tests.fakes.fake_adb import FakeAdb


def test_qr_payload_is_androids_wireless_debugging_format() -> None:
    qr = new_qr()
    assert qr.payload == f"WIFI:T:ADB;S:{qr.name};P:{qr.password};;"
    assert qr.name.startswith("studio-tablink-") and len(qr.password) == 10
    assert new_qr().password != qr.password
    assert qr.png().startswith(b"\x89PNG")


async def test_qr_pairing_finds_the_tablet_and_pairs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    qr = new_qr()
    fake = FakeAdb(usb_attached=False, pair_code=qr.password)
    fake.mdns = [(qr.name, "_adb-tls-pairing._tcp", "192.0.2.20:37001")]
    adb = Adb(runner=fake, adb_path="adb")
    assert await wait_and_pair_qr(adb, qr, timeout=5) == "192.0.2.20"
    assert qr.password not in caplog.text  # the pairing secret is never logged


async def test_qr_pairing_times_out_with_advice() -> None:
    adb = Adb(runner=FakeAdb(usb_attached=False), adb_path="adb")
    with pytest.raises(WifiPairError, match="6-digit"):
        await wait_and_pair_qr(adb, new_qr(), timeout=0.05)


async def test_code_must_be_six_digits() -> None:
    adb = Adb(runner=FakeAdb(), adb_path="adb")
    with pytest.raises(WifiPairError):
        await pair_with_code(adb, "192.0.2.20", 37001, "12ab")
    assert await pair_with_code(adb, "192.0.2.20", 37001, "123456") == "192.0.2.20"


async def test_connect_then_switch_to_tcp_5555() -> None:
    fake = FakeAdb(usb_attached=False, reachable_tcp={"192.0.2.20:41927"})
    fake.mdns = [("adb-X", "_adb-tls-connect._tcp", "192.0.2.20:41927")]
    adb = Adb(runner=fake, adb_path="adb")
    serial = await connect_after_pairing(adb, "192.0.2.20", timeout=5)
    assert serial == "192.0.2.20:5555" and fake.tcpip_enabled


async def test_rescue_reconnects_after_tablet_restart() -> None:
    from tablink.daemon import _AdbRescue
    from tablink.paths.manager import PathManager

    fake = FakeAdb(usb_attached=False, reachable_tcp={"192.0.2.20:41927"})
    fake.mdns = [("adb-X", "_adb-tls-connect._tcp", "192.0.2.20:41927")]
    adb = Adb(runner=fake, adb_path="adb")
    rescue = _AdbRescue(adb, PathManager([]))
    assert await rescue.check() is True and fake.tcpip_enabled
    assert await rescue.check() is False  # a device is connected now: nothing to do
