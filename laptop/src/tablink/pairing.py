"""`tablink pair`: one-time setup over USB. Every step is skipped when already done.

1. Find the tablet and check the developer switches HyperOS needs.
2. Install the TabLink tablet app (remote desktop, files, clipboard and notifications in one).
3. Turn on GNOME Remote Desktop with a random password.
4. Give the password to TabLink, which keeps it encrypted (no login box ever).
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
from tablink.secrets import Store
from tablink.server import TabLinkServer

log = logging.getLogger(__name__)

FDROID = "https://f-droid.org/repo/"
TABLINK_PACKAGE = "dev.tablink"
PENDING_PASSWORD_FILE = "rdp-password.pending"  # LaptopConnection.PENDING_FILE in the app
PENDING_TOKEN_FILE = "control-token.pending"  # Secrets.PENDING_TOKEN_FILE in the app
PENDING_PRIVATE_FILE = "private-network.pending"  # Tailnet.PENDING_PRIVATE_FILE in the app
EXTENSION_UUID = "tablink@dev.tablink"


@dataclass(frozen=True)
class Apk:
    package: str
    version_code: int
    file: str
    sha256: str
    abi: str = ""  # "" = works on every CPU


# Third-party tablet apps TabLink needs. None today: remote desktop (FreeRDP), files,
# clipboard and notifications are all built into the TabLink app. Entries are pinned builds,
# verified against F-Droid's index (sha256).
APKS: list[Apk] = []

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


def extension_source() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = parent / "data" / "gnome-shell-extension" / EXTENSION_UUID
        if p.is_dir():
            return p
    return None


def enable_gnome_extension(uuid: str) -> bool:
    """Add the extension to GNOME's enabled list. Returns False if it already was."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    settings = Gio.Settings.new("org.gnome.shell")
    enabled = list(settings.get_strv("enabled-extensions"))
    if uuid in enabled:
        return False
    settings.set_strv("enabled-extensions", [*enabled, uuid])
    Gio.Settings.sync()
    return True


class Pairing:
    def __init__(
        self,
        adb: Adb,
        progress: Progress,
        rd: object | None = None,
        cfg_load: Callable[[], config_mod.Config] = config_mod.load,
        cfg_save: Callable[[config_mod.Config], object] = config_mod.save,
        fetch: Callable[[Apk], object] | None = None,
        token_store: Store | None = None,
        extensions_dir: Path | None = None,
        enable_extension: Callable[[str], bool] | None = None,
        server: TabLinkServer | None = None,
    ) -> None:
        self.server = server
        self.token_store = token_store
        self.extensions_dir = extensions_dir or (
            Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
        )
        self.enable_extension = enable_extension or enable_gnome_extension
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
        elif await self._same_build_installed(serial, own, TABLINK_PACKAGE):
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

    async def give_password_to_tablink(self, serial: str, password: str) -> None:
        """Hand the remote-desktop password to the TabLink app, which keeps it encrypted.

        It goes through adb's stdin into the app's private storage; TabLink moves it into its
        Android-Keystore-encrypted database on start and deletes the file.
        """
        self.progress("run", "Giving the TabLink app the laptop login")
        await self.adb.write_app_file(
            serial, TABLINK_PACKAGE, PENDING_PASSWORD_FILE, password.encode()
        )
        self.progress("ok", "TabLink can open the laptop (no login box)")

    async def pair_token(self, serial: str) -> None:
        """Share the control-channel secret: kept in the GNOME keyring and in TabLink."""
        from tablink.secrets import KeyringStore, control_token

        self.progress("run", "Pairing the TabLink app with this laptop")
        store = self.token_store or KeyringStore()
        token = control_token(store, create=True)
        assert token is not None
        await self.adb.write_app_file(serial, TABLINK_PACKAGE, PENDING_TOKEN_FILE, token.encode())
        # Restart TabLink so it takes in both the login and the token now.
        await self.adb.shell(serial, "am", "force-stop", TABLINK_PACKAGE)
        await self.adb.shell(serial, "am", "start", "-n", f"{TABLINK_PACKAGE}/.MainActivity")
        self.progress("ok", "Paired (secret kept in your keyring and encrypted on the tablet)")

    async def private_network(self, serial: str) -> None:
        """Give the tablet the address of TabLink's own server and a one-time join key."""
        import json

        self.progress("run", "Adding the tablet to TabLink's private network")
        server = self.server or TabLinkServer()
        url = await server.ensure_running()
        key = await server.join_key()
        data = json.dumps({"server_url": url, "join_key": key}).encode()
        await self.adb.write_app_file(serial, TABLINK_PACKAGE, PENDING_PRIVATE_FILE, data)
        self.progress(
            "ok", "Private network ready (only your laptop and tablet, end-to-end encrypted)"
        )

    def clipboard_extension(self) -> None:
        """Install the small GNOME Shell extension that lets TabLink share the clipboard."""
        import shutil

        src = extension_source()
        if src is None:
            self.progress("skip", "Clipboard extension: source not found")
            return
        dest = self.extensions_dir / EXTENSION_UUID
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, dest / f.name)
        if self.enable_extension(EXTENSION_UUID):
            self.progress("ok", "Clipboard extension installed (works after your next login)")
        else:
            self.progress("skip", "Clipboard extension already enabled")

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
        await self.give_password_to_tablink(dev.serial, password)
        await self.private_network(dev.serial)
        await self.pair_token(dev.serial)  # last: restarts TabLink so it takes everything in
        self.clipboard_extension()
        await self.wifi(dev.serial)
        self.progress("ok", "All set. Tap TabLink on the tablet, or click TabLink on the laptop.")
