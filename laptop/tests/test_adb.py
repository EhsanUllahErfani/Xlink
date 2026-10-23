from pathlib import Path

import pytest

from tablink import adb as adb_mod
from tablink.adb import Adb, AdbError, AdbNotFound, Device, parse_devices
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb


@pytest.fixture
def fake() -> FakeAdb:
    return FakeAdb()


@pytest.fixture
def adb(fake: FakeAdb) -> Adb:
    return Adb(runner=fake, adb_path="adb")


async def test_devices_lists_usb_tablet(adb: Adb) -> None:
    [d] = await adb.devices()
    assert d == Device(USB_SERIAL, "device", "usb", "24074RPD2G", "taiko_global")
    assert (await adb.usb_device()) == d


async def test_no_usb_device_when_unplugged_or_unauthorized(fake: FakeAdb, adb: Adb) -> None:
    fake.authorized = False
    assert await adb.usb_device() is None
    fake.usb_attached = False
    assert await adb.devices() == []


def test_parse_devices_handles_mdns_and_noise() -> None:
    text = (
        "* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n"
        "List of devices attached\n"
        "adb-TAIKO-abc._adb-tls-connect._tcp device product:taiko model:Pad transport_id:3\n"
        "192.0.2.4:5555 offline transport_id:4\n"
        "\n"
    )
    devs = parse_devices(text)
    assert [(d.serial, d.state, d.transport) for d in devs] == [
        ("adb-TAIKO-abc._adb-tls-connect._tcp", "device", "tcp"),
        ("192.0.2.4:5555", "offline", "tcp"),
    ]
    assert not devs[1].ready


async def test_tcpip_then_connect(fake: FakeAdb, adb: Adb) -> None:
    with pytest.raises(AdbError, match="Connection refused"):
        await adb.connect("192.0.2.20")
    await adb.tcpip(USB_SERIAL)
    assert await adb.connect("192.0.2.20") == "192.0.2.20:5555"
    assert await adb.connect("192.0.2.20") == "192.0.2.20:5555"  # already connected
    assert {d.serial for d in await adb.devices()} == {USB_SERIAL, "192.0.2.20:5555"}
    await adb.disconnect("192.0.2.20:5555")
    assert [d.serial for d in await adb.devices()] == [USB_SERIAL]


async def test_connect_ipv6_brackets(fake: FakeAdb, adb: Adb) -> None:
    fake.reachable_tcp.add("[fd7a::1]:40001")
    assert await adb.connect("fd7a::1", 40001) == "[fd7a::1]:40001"


async def test_pair_success_and_code_never_logged(
    fake: FakeAdb, adb: Adb, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    await adb.pair("192.0.2.20", 37001, "123456")
    with pytest.raises(AdbError) as exc:
        await adb.pair("192.0.2.20", 37001, "999999")
    assert "999999" not in str(exc.value)
    assert "123456" not in caplog.text and "999999" not in caplog.text


async def test_forward_and_reverse(fake: FakeAdb, adb: Adb) -> None:
    await adb.reverse(USB_SERIAL, 47800, 47800)
    await adb.reverse(USB_SERIAL, 22000, 22000)
    assert sorted(await adb.reverse_list(USB_SERIAL)) == [(22000, 22000), (47800, 47800)]
    await adb.reverse_remove(USB_SERIAL, 22000)
    assert await adb.reverse_list(USB_SERIAL) == [(47800, 47800)]
    await adb.forward(USB_SERIAL, 27183, 27183)
    assert fake.forwards[USB_SERIAL] == {27183: 27183}
    await adb.forward_remove(USB_SERIAL, 27183)
    assert fake.forwards[USB_SERIAL] == {}


async def test_shell_and_props(adb: Adb) -> None:
    assert (await adb.shell(USB_SERIAL, "echo", "hi")).strip() == "hi"
    assert await adb.getprop(USB_SERIAL, "ro.product.device") == "taiko"


async def test_wifi_addrs_only_wifi_interfaces(adb: Adb) -> None:
    assert await adb.wifi_addrs(USB_SERIAL) == ["192.0.2.20"]


async def test_unknown_serial_raises(adb: Adb) -> None:
    with pytest.raises(AdbError, match="not found"):
        await adb.shell("nope", "true")


async def test_command_failure_raises_with_message(fake: FakeAdb, adb: Adb) -> None:
    fake.fail_next = "error: no devices/emulators found"
    with pytest.raises(AdbError, match="no devices"):
        await adb.devices()


async def test_timeout_becomes_adb_error(fake: FakeAdb, adb: Adb) -> None:
    fake.timeout_next = True
    with pytest.raises(AdbError, match="timed out"):
        await adb.devices()


async def test_install(fake: FakeAdb, adb: Adb, tmp_path: Path) -> None:
    await adb.install(USB_SERIAL, tmp_path / "app.apk")
    assert fake.calls[-1][-3:] == ["install", "-r", str(tmp_path / "app.apk")]


async def test_serial_flag_comes_first(fake: FakeAdb, adb: Adb) -> None:
    await adb.tcpip(USB_SERIAL)
    assert fake.calls[-1] == ["-s", USB_SERIAL, "tcpip", "5555"]


def test_find_adb_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLINK_ADB", "/opt/adb")
    assert adb_mod.find_adb() == "/opt/adb"


def test_find_adb_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TABLINK_ADB", raising=False)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(AdbNotFound):
        adb_mod.find_adb()


async def test_real_subprocess_runner_runs_a_program() -> None:
    from tablink.proc import subprocess_runner

    res = await subprocess_runner(["true"], 5)
    assert res.returncode == 0
