"""`tablink pair`: one-time setup over USB. Every step is skipped when already done.

1. Find the tablet and check the developer switches HyperOS needs.
2. Install the tablet apps (TabLink, aFreeRDP, KDE Connect), checksums verified.
3. Turn on GNOME Remote Desktop with a random password.
4. Create aFreeRDP's saved "Laptop" connection (no login box afterwards).
5. Prepare Wi-Fi (adb over TCP) and remember the tablet.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tablink import config as config_mod
from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError, Device
from tablink.afreerdp_setup import AFreeRdpSetup
from tablink.remote_session import AFREERDP

log = logging.getLogger(__name__)

FDROID = "https://f-droid.org/repo/"


@dataclass(frozen=True)
class Apk:
    package: str
    version_code: int
    file: str
    sha256: str
    abi: str = ""  # "" = works on every CPU


# Pinned builds, verified against F-Droid's index (sha256) when they were added.
APKS = [
    Apk(
        "org.kde.kdeconnect_tp",
        13600,
        "org.kde.kdeconnect_tp_13600.apk",
        "ab8f655b3f3a4a9dacace69cfea4d062fe115f7910c33f14802b589e17bd8b37",
    ),
    Apk(
        AFREERDP,
        3320103,
        "com.freerdp.afreerdp_3320103.apk",
        "520403bf41dec2e4599ddd22ab2bd7597f64867ec422c5e2fa46da259cac668e",
        abi="arm64-v8a",
    ),
    Apk(
        AFREERDP,
        3320102,
        "com.freerdp.afreerdp_3320102.apk",
        "0fca0a60d1854ace8c2c768919c7e4806b3b4bebdfb03a1609314b617158c535",
        abi="armeabi-v7a",
    ),
]

Progress = Callable[[str, str], None]  # (state, text): state is "run", "ok", "skip", "wait", "fail"


class PairError(RuntimeError):
    """A step needs the user. The message says exactly what to do."""


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tablink" / "apks"


def pick_apks(abis: list[str]) -> list[Apk]:
    chosen: dict[str, Apk] = {}
    for apk in APKS:
        if apk.package in chosen:
            continue
        if not apk.abi or apk.abi in abis:
            chosen[apk.package] = apk
    return list(chosen.values())


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


async def fetch_verified(apk: Apk, cache: Path | None = None) -> Path:
    """Download (or reuse) an APK and check its pinned sha256. Raises PairError on mismatch."""
    cache = cache or cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / apk.file
    if not dest.exists() or await asyncio.to_thread(_sha256, dest) != apk.sha256:
        await asyncio.to_thread(_download, FDROID + apk.file, dest)
    if await asyncio.to_thread(_sha256, dest) != apk.sha256:
        dest.unlink(missing_ok=True)
        raise PairError(f"{apk.file} failed its checksum check, so it was not installed.")
    return dest


def tablink_apk() -> Path | None:
    """The TabLink tablet app: $TABLINK_APK, or the build in this source tree."""
    if env := os.environ.get("TABLINK_APK"):
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = parent / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        if p.exists():
            return p
    return None


class Pairing:
    def __init__(
        self,
        adb: Adb,
        progress: Progress,
        rd: object | None = None,
        cfg_load: Callable[[], config_mod.Config] = config_mod.load,
        cfg_save: Callable[[config_mod.Config], object] = config_mod.save,
        fetch: Callable[[Apk], object] | None = None,
    ) -> None:
        self.adb = adb
        self.progress = progress
        self.rd = rd
        self.cfg_load = cfg_load
        self.cfg_save = cfg_save
        self.fetch = fetch or fetch_verified

    async def find_tablet(self) -> Device:
        self.progress("run", "Looking for the tablet on USB")
        devices = [d for d in await self.adb.devices() if d.transport == "usb"]
        if any(d.state == "unauthorized" for d in devices):
            raise PairError(
                'Unlock the tablet and tap "Allow" on the "Allow USB debugging?" prompt '
                '(tick "Always allow"), then run `tablink pair` again.'
            )
        ready = [d for d in devices if d.ready]
        if not ready:
            raise PairError(
                "No tablet on USB. Plug it in with a USB-C cable and turn on "
                "Settings > Additional settings > Developer options > USB debugging."
            )
        dev = ready[0]
        name = await self.adb.getprop(dev.serial, "ro.product.marketname")
        self.progress("ok", f"Found {name or dev.model or dev.serial}")
        return dev

    async def check_switches(self, serial: str) -> None:
        self.progress("run", "Checking the tablet's developer switches")
        missing = []
        # Xiaomi/HyperOS only; other tablets do not have these properties.
        if await self.adb.getprop(serial, "persist.security.adbinput") == "0":
            missing.append("USB debugging (Security settings)")
        if await self.adb.getprop(serial, "persist.security.adbinstall") == "0":
            missing.append("Install via USB")
        if missing:
            raise PairError(
                "On the tablet, open Settings > Additional settings > Developer options and "
                f"turn on: {', '.join(missing)}. Then run `tablink pair` again."
            )
        self.progress("ok", "Developer switches are on")

    async def installed_version(self, serial: str, package: str) -> int:
        out = await self.adb.shell(serial, "pm", "list", "packages", "--show-versioncode", package)
        for line in out.splitlines():
            if line.startswith(f"package:{package} "):
                _, _, code = line.partition("versionCode:")
                return int(code.strip() or 0)
        return 0

    async def install_apps(self, serial: str) -> None:
        abis = (await self.adb.getprop(serial, "ro.product.cpu.abilist")).split(",")
        for apk in pick_apks(abis):
            if await self.installed_version(serial, apk.package) >= apk.version_code:
                self.progress("skip", f"{apk.package} is already installed")
                continue
            self.progress("run", f"Downloading {apk.package}")
            path = await self.fetch(apk)  # type: ignore[misc]
            await self._install(serial, Path(str(path)), apk.package)
        own = tablink_apk()
        if own is None:
            self.progress("skip", "TabLink tablet app: no build found (set TABLINK_APK)")
        elif await self._same_build_installed(serial, own, "dev.tablink"):
            self.progress("skip", "TabLink tablet app is up to date")
        else:
            await self._install(serial, own, "TabLink")

    async def _same_build_installed(self, serial: str, apk: Path, package: str) -> bool:
        out = await self.adb.shell(serial, "pm", "path", package)
        paths = [ln.removeprefix("package:").strip() for ln in out.splitlines() if "base.apk" in ln]
        if not paths:
            return False
        remote = (await self.adb.shell(serial, "sha256sum", paths[0])).split()
        return bool(remote) and remote[0] == await asyncio.to_thread(_sha256, apk)

    async def _install(self, serial: str, path: Path, name: str) -> None:
        self.progress("wait", f'Installing {name}: if the tablet asks, tap "Install"')
        try:
            await self.adb.install(serial, path)
        except AdbError as e:
            if "USER_RESTRICTED" in str(e):
                raise PairError(
                    f'The tablet refused to install {name}. Turn on "Install via USB" in '
                    'Developer options, run `tablink pair` again and tap "Install".'
                ) from e
            raise
        self.progress("ok", f"Installed {name}")

    async def remote_desktop(self) -> str:
        from tablink.services.remote_desktop import RemoteDesktop

        self.progress("run", "Turning on GNOME Remote Desktop")
        rd = self.rd if isinstance(self.rd, RemoteDesktop) else RemoteDesktop()
        await rd.setup()
        password = await rd.password()
        if not password:
            raise PairError("GNOME Remote Desktop has no password; run `tablink pair` again.")
        self.progress("ok", "Remote desktop is on (encrypted, random password in your keyring)")
        return password

    async def saved_connection(self, serial: str, password: str) -> None:
        from tablink.services.remote_desktop import RDP_PORT, RDP_USER

        cfg = self.cfg_load()
        self.progress("wait", "Creating aFreeRDP's saved login: please don't touch the tablet")
        created = await AFreeRdpSetup(self.adb, serial).run(
            "127.0.0.1", RDP_PORT, RDP_USER, password
        )
        if not cfg.remote.afreerdp_bookmark:
            cfg.remote.afreerdp_bookmark = 1  # aFreeRDP numbers saved connections from 1
            self.cfg_save(cfg)
        self.progress("ok" if created else "skip", "aFreeRDP saved login is ready")

    async def wifi(self, serial: str) -> None:
        self.progress("run", "Preparing Wi-Fi")
        cfg = self.cfg_load()
        for addr in reversed(await self.adb.wifi_addrs(serial)):
            cfg.remember_tablet_addr(addr)
        cfg.device.tablet_serial = serial
        self.cfg_save(cfg)
        if await self.adb.tcp_port(serial) != LEGACY_TCP_PORT:
            await self.adb.tcpip(serial, LEGACY_TCP_PORT)
            await self.adb.wait_for(serial)
        self.progress("ok", "Wi-Fi ready (redo after a tablet restart: plug in once)")

    async def run(self) -> None:
        dev = await self.find_tablet()
        await self.check_switches(dev.serial)
        await self.install_apps(dev.serial)
        password = await self.remote_desktop()
        await self.saved_connection(dev.serial, password)
        await self.wifi(dev.serial)
        self.progress("ok", "All set. Tap TabLink on the tablet, or click TabLink on the laptop.")
