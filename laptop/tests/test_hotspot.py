from pathlib import Path

import pytest

from tablink.adb import Adb
from tablink.nmcli import CON_NAME, Nmcli, NmError, new_password, parse_devices
from tablink.paths.lan import LanPath
from tablink.paths.netinfo import neighbours
from tests.fakes.fake_adb import FakeAdb
from tests.fakes.fake_nmcli import FakeNmcli


def test_parse_devices_handles_escaped_colons() -> None:
    text = "wlo1:wifi:connected:Cafe\\: Free\nenp3s0:ethernet:unavailable:\n"
    [d] = parse_devices(text)
    assert (d.name, d.state, d.connection) == ("wlo1", "connected", "Cafe: Free")


async def test_hotspot_on_keeps_password_off_command_lines() -> None:
    fake = FakeNmcli()
    nm = Nmcli(fake)
    pw = new_password()
    assert await nm.hotspot_on("TabLink", pw) == "wlo1"
    assert fake.psk_seen == pw
    assert all(pw not in " ".join(c) for c in fake.calls)
    assert fake.passwd_file and not Path(fake.passwd_file).exists()  # deleted at once
    add = next(c for c in fake.calls if c[:2] == ["connection", "add"])
    assert "wifi-sec.psk-flags" in add and add[add.index("wifi-sec.psk-flags") + 1] == "2"
    assert await nm.hotspot_device() == "wlo1"


async def test_hotspot_reuses_connection_and_turns_off() -> None:
    fake = FakeNmcli(connections={"SIT-Secure", CON_NAME})
    nm = Nmcli(fake)
    await nm.hotspot_on("TabLink", new_password())
    assert not any(c[:2] == ["connection", "add"] for c in fake.calls)
    assert await nm.hotspot_off() is True
    assert await nm.hotspot_off() is False  # already off


async def test_failed_start_is_reported_and_file_removed() -> None:
    fake = FakeNmcli(fail_up=True)
    with pytest.raises(NmError, match="No suitable device"):
        await Nmcli(fake).hotspot_on("TabLink", new_password())
    assert fake.passwd_file is None or not Path(fake.passwd_file).exists()


def test_new_passwords_are_strong() -> None:
    pws = {new_password() for _ in range(20)}
    assert len(pws) == 20 and all(len(p) == 16 and p.isalnum() for p in pws)


def test_neighbours_reads_complete_entries_on_one_interface(tmp_path: Path) -> None:
    arp = tmp_path / "arp"
    arp.write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "10.42.0.57       0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlo1\n"
        "10.42.0.99       0x1         0x0         00:00:00:00:00:00     *        wlo1\n"
        "10.4.148.1       0x1         0x2         11:22:33:44:55:66     *        enp3s0\n"
    )
    assert neighbours("wlo1", arp) == ["10.42.0.57"]


async def test_lan_path_finds_tablet_on_laptop_hotspot() -> None:
    fake = FakeAdb(usb_attached=False, tcpip_enabled=True)

    async def reach(addr: str, port: int) -> bool:
        return addr == "10.42.0.57"

    async def iface() -> str | None:
        return "wlo1"

    lan = LanPath(
        Adb(runner=fake, adb_path="adb"),
        lambda: ["192.0.2.20"],  # the old campus address is stale now
        hotspot_iface=iface,
        reachable=reach,
        gateways=lambda: [],
        local_addr=lambda a: "10.42.0.1",
    )
    lan.neighbours = lambda i: ["10.42.0.57"]  # type: ignore[method-assign,assignment]
    path = await lan.probe()
    assert path is not None and path.mode == "laptop_hotspot"
    assert (path.tablet_addr, path.laptop_addr) == ("10.42.0.57", "10.42.0.1")
    assert path.preset == "balanced"
