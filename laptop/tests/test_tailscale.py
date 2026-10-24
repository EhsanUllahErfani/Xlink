import json

from tablink.adb import Adb
from tablink.paths.tailscale import LOCAL_ADB_PORT, TailscalePath
from tablink.secrets import MemoryStore
from tablink.tailscale import Peer, TailnetStatus, Tailscale, find_tablet
from tests.fakes.fake_adb import FakeAdb
from tests.fakes.fake_tailnet import FakeHelper


def node(helper: FakeHelper, store: MemoryStore | None = None) -> Tailscale:
    async def launch() -> FakeHelper:
        return helper

    return Tailscale(store or MemoryStore(), launch=launch, hostname="tablink-laptop")


async def test_keys_go_to_the_keyring_store_and_come_back() -> None:
    store = MemoryStore()
    helper = FakeHelper()
    ts = node(helper, store)
    await ts.status()
    saved = json.loads(store.get() or "{}")
    assert "_machinekey" in saved
    await ts.stop()
    helper2 = FakeHelper()
    ts2 = node(helper2, store)
    await ts2.status()
    assert helper2.init is not None and helper2.init["state"] == saved  # restored from keyring
    await ts2.stop()


async def test_internet_path_relays_adb_through_the_helper() -> None:
    helper = FakeHelper()
    fake = FakeAdb(usb_attached=False, reachable_tcp={f"127.0.0.1:{LOCAL_ADB_PORT}"})
    path = TailscalePath(Adb(runner=fake, adb_path="adb"), node(helper), lambda: True)
    p = await path.probe()
    assert p is not None
    assert (p.mode, p.tablet_addr, p.laptop_addr) == ("tailscale", "100.64.0.7", "100.64.0.1")
    assert p.adb_serial == f"127.0.0.1:{LOCAL_ADB_PORT}" and p.preset == "low"
    assert helper.forwards == {f"127.0.0.1:{LOCAL_ADB_PORT}": "100.64.0.7:5555"}
    await path.activate(p)
    assert fake.reverses.get(p.adb_serial, {}) == {}  # no adb tunnel: the app routes itself
    await path.tailscale.stop()


async def test_off_or_not_signed_in_gives_no_path() -> None:
    fake = FakeAdb(usb_attached=False)
    assert (
        await TailscalePath(
            Adb(runner=fake, adb_path="adb"), node(FakeHelper()), lambda: False
        ).probe()
        is None
    )
    ts = node(FakeHelper(state="NeedsLogin"))
    assert await TailscalePath(Adb(runner=fake, adb_path="adb"), ts, lambda: True).probe() is None
    st = await ts.status()
    assert st.auth_url.startswith("https://login.tailscale.com/")
    await ts.stop()


def test_find_tablet_prefers_tablinks_own_node() -> None:
    def p(host: str, os: str = "android") -> Peer:
        return Peer(host, os, ("100.64.0.5",), True)

    st = TailnetStatus(True, ("100.64.0.1",), (p("pixel-phone"), p("tablink-2505drp06g")))
    tab = find_tablet(st)
    assert tab is not None and tab.hostname == "tablink-2505drp06g"
    two = TailnetStatus(True, (), (p("phone-a"), p("phone-b")))
    assert find_tablet(two) is None  # never guess between two Androids
