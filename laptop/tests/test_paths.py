from dataclasses import dataclass, field
from pathlib import Path as FsPath

from tablink.adb import Adb
from tablink.paths.base import Path
from tablink.paths.lan import LanPath, classify
from tablink.paths.manager import PathManager
from tablink.paths.netinfo import default_gateways
from tablink.paths.usb import UsbPath
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb

USB = Path("usb", "127.0.0.1", "127.0.0.1", "S", 2.0, True)
LAN = Path("lan", "192.0.2.20", "192.0.2.5", "192.0.2.20:5555", 9.0, True)
BT = Path("bluetooth", "192.0.2.30", "192.0.2.31", None, 80.0, False)


@dataclass
class FakeProvider:
    mode: str
    priority: int
    path: Path | None
    activations: int = 0
    deactivations: int = 0
    probes: int = 0

    async def probe(self) -> Path | None:
        self.probes += 1
        return self.path

    async def activate(self, path: Path) -> None:
        self.activations += 1

    async def deactivate(self) -> None:
        self.deactivations += 1


@dataclass
class Recorder:
    name: str = "rec"
    needs_video: bool = False
    seen: list[str | None] = field(default_factory=list)

    async def on_path_changed(self, path: Path | None) -> None:
        self.seen.append(path.mode if path else None)


def setup() -> tuple[FakeProvider, FakeProvider, FakeProvider, Recorder, PathManager]:
    usb = FakeProvider("usb", 0, USB)
    lan = FakeProvider("lan", 10, LAN)
    bt = FakeProvider("bluetooth", 30, BT)
    rec = Recorder()
    return usb, lan, bt, rec, PathManager([bt, lan, usb], [rec])


async def test_picks_highest_priority_working_path() -> None:
    usb, lan, _, rec, mgr = setup()
    assert (await mgr.check()) == USB
    assert rec.seen == ["usb"] and usb.activations == 1
    assert lan.probes == 0  # worse paths are not probed while a better one works


async def test_simulated_usb_loss_switches_to_next_path_and_back() -> None:
    usb, lan, _, rec, mgr = setup()
    await mgr.check()
    usb.path = None  # cable pulled
    assert (await mgr.check()) == LAN
    assert usb.deactivations == 1 and lan.activations == 1
    usb.path = USB  # cable back
    assert (await mgr.check()) == USB
    assert rec.seen == ["usb", "lan", "usb"]


async def test_steady_path_does_not_notify_again() -> None:
    _, _, _, rec, mgr = setup()
    await mgr.check()
    await mgr.check()
    await mgr.check()
    assert rec.seen == ["usb"]


async def test_nothing_works_reports_none() -> None:
    usb, lan, bt, rec, mgr = setup()
    usb.path = lan.path = bt.path = None
    assert await mgr.check() is None
    await mgr.check()
    assert rec.seen == []  # services start with no path; nothing changed


async def test_forced_mode_pins_one_path() -> None:
    usb, lan, bt, rec, _ = setup()
    mgr = PathManager([usb, lan, bt], [rec], forced_mode="bluetooth")
    assert (await mgr.check()) == BT
    assert usb.probes == 0


async def test_crashing_provider_and_service_do_not_stop_manager() -> None:
    usb, lan, _, rec, _ = setup()

    async def boom() -> Path | None:
        raise RuntimeError("probe exploded")

    usb.probe = boom  # type: ignore[method-assign]

    class Bad:
        name = "bad"
        needs_video = False

        async def on_path_changed(self, path: Path | None) -> None:
            raise RuntimeError("service exploded")

    mgr = PathManager([usb, lan], [Bad(), rec])
    assert (await mgr.check()) == LAN
    assert rec.seen == ["lan"]


def test_classify_modes() -> None:
    assert classify("192.0.2.20", ["192.0.2.1"], False) == "lan"
    assert classify("192.0.2.1", ["192.0.2.1"], False) == "tablet_hotspot"
    assert classify("10.42.0.50", ["192.0.2.1"], True) == "laptop_hotspot"


def test_default_gateways_parses_proc_route(tmp_path: FsPath) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "wlo1\t00000000\t0100040A\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "wlo1\t0000040A\t00000000\t0001\t0\t0\t600\t00FCFFFF\t0\t0\t0\n"
    )
    assert default_gateways(route) == ["192.168.1.1", "10.4.0.1"]
    assert default_gateways(tmp_path / "missing") == []


def make_lan(fake: FakeAdb, known: list[str], open_ports: set[str]) -> LanPath:
    async def reach(addr: str, port: int) -> bool:
        return addr in open_ports

    return LanPath(
        Adb(runner=fake, adb_path="adb"),
        lambda: known,
        reachable=reach,
        gateways=lambda: ["192.0.2.1"],
        local_addr=lambda a: "192.0.2.5",
    )


async def test_lan_probe_connects_to_known_address() -> None:
    fake = FakeAdb(usb_attached=False, tcpip_enabled=True)
    lan = make_lan(fake, ["192.0.2.99", "192.0.2.20"], {"192.0.2.20"})
    path = await lan.probe()
    assert path is not None
    assert (path.mode, path.tablet_addr, path.laptop_addr) == ("lan", "192.0.2.20", "192.0.2.5")
    assert path.adb_serial == "192.0.2.20:5555" and path.video_ok


async def test_lan_probe_finds_tablet_as_gateway() -> None:
    fake = FakeAdb(usb_attached=False, tcpip_enabled=True)
    lan = make_lan(fake, [], {"192.0.2.1"})
    path = await lan.probe()
    assert path is not None and path.mode == "tablet_hotspot"


async def test_lan_probe_none_when_unreachable_or_tcp_off() -> None:
    assert await make_lan(FakeAdb(usb_attached=False), ["192.0.2.20"], set()).probe() is None
    fake = FakeAdb(usb_attached=False, tcpip_enabled=False)
    assert await make_lan(fake, ["192.0.2.20"], {"192.0.2.20"}).probe() is None


async def test_usb_activate_prepares_wifi_once() -> None:
    fake = FakeAdb()
    learned: list[list[str]] = []
    usb = UsbPath(Adb(runner=fake, adb_path="adb"), 47800, prepare_wifi=True,
                  on_wifi_addrs=learned.append)  # fmt: skip
    path = await usb.probe()
    assert path
    await usb.activate(path)
    assert fake.tcpip_enabled and learned == [["192.0.2.20"]]
    assert fake.reverses[USB_SERIAL][47800] == 47800  # tunnel set after adbd restart
    n = len([c for c in fake.calls if "tcpip" in c])
    await usb.activate(path)
    assert len([c for c in fake.calls if "tcpip" in c]) == n  # not restarted again
