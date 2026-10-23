from tablink.adb import Adb
from tablink.paths.tailscale import TailscalePath
from tablink.tailscale import Tailscale, find_tablet, parse_status
from tests.fakes.fake_adb import FakeAdb
from tests.fakes.fake_tailscale import FakeTailscale


def make(
    ts: FakeTailscale, enabled: bool = True, open_port: bool = True
) -> tuple[TailscalePath, FakeAdb]:
    fake = FakeAdb(usb_attached=False, tcpip_enabled=True)

    async def reach(addr: str, port: int) -> bool:
        return open_port

    path = TailscalePath(
        Adb(runner=fake, adb_path="adb"), Tailscale(ts), lambda: enabled,
        name_hint=lambda: "Redmi Pad 2", reachable=reach,
    )  # fmt: skip
    return path, fake


async def test_tailscale_path_reaches_tablet_over_tailnet() -> None:
    path_provider, fake = make(FakeTailscale())
    p = await path_provider.probe()
    assert p is not None
    assert (p.mode, p.tablet_addr, p.laptop_addr) == ("tailscale", "100.64.0.7", "100.64.0.1")
    assert p.adb_serial == "100.64.0.7:5555" and p.preset == "low"
    await path_provider.activate(p)
    assert fake.reverses["100.64.0.7:5555"][47800] == 47800


async def test_disabled_not_installed_stopped_or_offline_give_none() -> None:
    assert await make(FakeTailscale(), enabled=False)[0].probe() is None
    assert await make(FakeTailscale(installed=False))[0].probe() is None
    assert await make(FakeTailscale(running=False))[0].probe() is None
    ts = FakeTailscale()
    for peer in ts.peers:
        peer["Online"] = False
    assert await make(ts)[0].probe() is None
    assert await make(FakeTailscale(), open_port=False)[0].probe() is None


def test_find_tablet_prefers_name_then_only_android() -> None:
    import json

    def peer(name: str, ip: str) -> dict[str, object]:
        return {"HostName": name, "OS": "android", "Online": True, "TailscaleIPs": [ip]}

    st = parse_status(
        json.dumps(
            {
                "BackendState": "Running",
                "Self": {"TailscaleIPs": ["100.64.0.1"]},
                "Peer": {"a": peer("phone", "100.64.0.5"), "b": peer("redmi-pad-2", "100.64.0.7")},
            }
        )
    )
    tablet = find_tablet(st, "Redmi Pad 2")
    assert tablet is not None and tablet.hostname == "redmi-pad-2"
    assert find_tablet(st, "") is None  # two Androids and no name: refuse to guess


def test_garbage_status_is_not_running() -> None:
    assert parse_status("not json").running is False
