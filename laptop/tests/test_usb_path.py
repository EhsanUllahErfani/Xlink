from tablink.adb import Adb
from tablink.paths.usb import UsbPath
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb


def make(fake: FakeAdb, hint: str = "") -> UsbPath:
    return UsbPath(Adb(runner=fake, adb_path="adb"), 47800, serial_hint=hint)


async def test_probe_finds_usb_tablet() -> None:
    path = await make(FakeAdb()).probe()
    assert path is not None
    assert path.mode == "usb" and path.adb_serial == USB_SERIAL
    assert path.laptop_addr == "127.0.0.1" and path.video_ok
    assert path.preset == "high"
    assert path.rtt_ms >= 0


async def test_probe_none_when_unplugged_or_unauthorized() -> None:
    assert await make(FakeAdb(usb_attached=False)).probe() is None
    assert await make(FakeAdb(authorized=False)).probe() is None


async def test_probe_ignores_wifi_devices() -> None:
    fake = FakeAdb(usb_attached=False, connected_tcp=["192.0.2.20:5555"])
    assert await make(fake).probe() is None


async def test_probe_survives_adb_failure() -> None:
    fake = FakeAdb()
    fake.fail_next = "adb server version mismatch"
    assert await make(fake).probe() is None


async def test_activate_reverses_every_service_port_then_cleans_up() -> None:
    fake = FakeAdb()
    usb = make(fake)
    path = await usb.probe()
    assert path
    await usb.activate(path)
    assert fake.reverses[USB_SERIAL] == {
        47800: 47800,
        3389: 3389,
        22000: 22000,
        1716: 1716,
        2222: 22,
        8080: 8080,
    }
    await usb.deactivate()
    assert fake.reverses[USB_SERIAL] == {}
    await usb.deactivate()  # twice is harmless


async def test_deactivate_after_cable_pulled_does_not_raise() -> None:
    fake = FakeAdb()
    usb = make(fake)
    path = await usb.probe()
    assert path
    await usb.activate(path)
    fake.usb_attached = False
    await usb.deactivate()
