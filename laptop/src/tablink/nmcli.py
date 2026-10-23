"""NetworkManager through nmcli. The only module that runs nmcli.

Mode 3, the laptop hotspot: a "TabLink-Hotspot" connection whose Wi-Fi password is kept in the
GNOME keyring, never stored by NetworkManager (psk-flags=2, "not saved") and never put on a
command line: it is handed to `nmcli connection up` through a private file that is deleted
right away.
"""

from __future__ import annotations

import os
import secrets
import string
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tablink.proc import Runner, RunResult, subprocess_runner

CON_NAME = "TabLink-Hotspot"
PASSWORD_ALPHABET = string.ascii_letters + string.digits


class NmError(RuntimeError):
    pass


@dataclass(frozen=True)
class WifiDevice:
    name: str
    state: str  # "connected", "disconnected", ...
    connection: str


def parse_devices(text: str) -> list[WifiDevice]:
    """Parse `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device`."""
    out = []
    for line in text.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        if len(parts) < 4:
            continue
        name, typ, state, con = (p.replace("\0", ":") for p in parts[:4])
        if typ == "wifi":
            out.append(WifiDevice(name, state, con))
    return out


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(16))


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "tablink"


class Nmcli:
    def __init__(self, runner: Runner | None = None) -> None:
        self._run = runner or subprocess_runner

    async def _nmcli(self, *args: str, check: bool = True, timeout: float = 30.0) -> RunResult:
        res = await self._run(["nmcli", *args], timeout)
        if check and res.returncode != 0:
            msg = (res.stderr or res.stdout).strip().splitlines()
            raise NmError(msg[-1] if msg else f"nmcli {args[0]} failed")
        return res

    async def wifi_devices(self) -> list[WifiDevice]:
        res = await self._nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
        return parse_devices(res.stdout)

    async def hotspot_device(self) -> str | None:
        """The Wi-Fi device currently running the TabLink hotspot, if any."""
        for d in await self.wifi_devices():
            if d.connection == CON_NAME and d.state.startswith("connected"):
                return d.name
        return None

    async def _ensure_connection(self, ifname: str, ssid: str) -> None:
        res = await self._nmcli("-t", "-f", "NAME", "connection", "show", check=False)
        exists = CON_NAME in res.stdout.splitlines()
        settings = [
            "ifname", ifname,
            "802-11-wireless.mode", "ap",
            "802-11-wireless.ssid", ssid,
            "ipv4.method", "shared",
            "ipv6.method", "ignore",
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.proto", "rsn",
            "wifi-sec.psk-flags", "2",  # NetworkManager never stores the password
            "connection.autoconnect", "no",
        ]  # fmt: skip
        if exists:
            await self._nmcli("connection", "modify", CON_NAME, *settings)
        else:
            await self._nmcli("connection", "add", "type", "wifi", "con-name", CON_NAME, *settings)

    async def hotspot_on(self, ssid: str, password: str, ifname: str | None = None) -> str:
        """Start the hotspot; returns the Wi-Fi device used."""
        if ifname is None:
            devices = await self.wifi_devices()
            if not devices:
                raise NmError("this laptop has no Wi-Fi device")
            ifname = devices[0].name
        await self._ensure_connection(ifname, ssid)
        folder = runtime_dir()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, path = tempfile.mkstemp(dir=folder, prefix=".hs-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(f"802-11-wireless-security.psk:{password}\n")
            await self._nmcli("connection", "up", CON_NAME, "passwd-file", path, timeout=45.0)
        finally:
            Path(path).unlink(missing_ok=True)  # noqa: ASYNC240 - tiny local file
        return ifname

    async def hotspot_off(self) -> bool:
        """Stop the hotspot. Returns False if it was not running."""
        if await self.hotspot_device() is None:
            return False
        await self._nmcli("connection", "down", CON_NAME)
        return True
