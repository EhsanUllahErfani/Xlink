"""Async wrapper around `adb`. The only module that runs adb.

Every call goes through a `Runner`, so tests swap in `tests/fakes/fake_adb.py` and need no device.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tablink.proc import Runner, RunResult, subprocess_runner

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15.0
LEGACY_TCP_PORT = 5555


class AdbError(RuntimeError):
    pass


class AdbNotFound(AdbError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str  # "device", "unauthorized", "offline", ...
    transport: Literal["usb", "tcp"]
    model: str = ""
    product: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"


def find_adb() -> str:
    """adb from $TABLINK_ADB, PATH, or the Android SDK."""
    if env := os.environ.get("TABLINK_ADB"):
        return env
    if found := shutil.which("adb"):
        return found
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")):
        if root and (p := Path(root) / "platform-tools" / "adb").exists():
            return str(p)
    sdk = Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"
    if sdk.exists():
        return str(sdk)
    raise AdbNotFound("adb not found: install android-tools-adb or the Android SDK platform-tools")


def parse_devices(text: str) -> list[Device]:
    """Parse `adb devices -l`."""
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state, *rest = parts
        props = dict(p.split(":", 1) for p in rest if ":" in p)
        transport: Literal["usb", "tcp"] = "usb" if "usb" in props else "tcp"
        devices.append(
            Device(
                serial=serial,
                state=state,
                transport=transport,
                model=props.get("model", ""),
                product=props.get("product", ""),
            )
        )
    return devices


_IPV4 = re.compile(r"\binet (\d{1,3}(?:\.\d{1,3}){3})/\d+")


def parse_ipv4_addrs(text: str) -> list[str]:
    """Parse `ip -o -4 addr show ...` output into addresses, skipping loopback."""
    return [a for a in _IPV4.findall(text) if not a.startswith("127.")]


def _host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Adb:
    def __init__(self, runner: Runner | None = None, adb_path: str | None = None) -> None:
        self._runner = runner or subprocess_runner
        self._adb_path = adb_path

    @property
    def adb_path(self) -> str:
        if self._adb_path is None:
            self._adb_path = find_adb()
        return self._adb_path

    async def _run(
        self,
        *args: str,
        serial: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        check: bool = True,
        quiet_args: bool = False,
    ) -> RunResult:
        argv = [self.adb_path]
        if serial:
            argv += ["-s", serial]
        argv += args
        log.debug("adb %s", "<hidden>" if quiet_args else " ".join(argv[1:]))
        try:
            res = await self._runner(argv, timeout)
        except FileNotFoundError as e:
            raise AdbNotFound(str(e)) from e
        except TimeoutError as e:
            raise AdbError(f"adb {args[0]} timed out after {timeout:.0f} s") from e
        if check and res.returncode != 0:
            msg = (res.stderr or res.stdout).strip() or f"exit code {res.returncode}"
            raise AdbError(f"adb {args[0]}: {msg}")
        return res

    async def devices(self) -> list[Device]:
        return parse_devices((await self._run("devices", "-l")).stdout)

    async def usb_device(self) -> Device | None:
        """The first authorised device attached by USB, if any."""
        for d in await self.devices():
            if d.transport == "usb" and d.ready:
                return d
        return None

    async def connect(self, host: str, port: int = LEGACY_TCP_PORT) -> str:
        """Connect over TCP and return the serial. Raises AdbError on failure."""
        target = _host_port(host, port)
        res = await self._run("connect", target, check=False, timeout=10.0)
        out = (res.stdout + res.stderr).strip()
        failed = re.search(r"cannot|failed|unable", out, re.IGNORECASE)
        if res.returncode == 0 and "connected to" in out and not failed:
            return target
        raise AdbError(f"adb connect {target}: {out or 'failed'}")

    async def disconnect(self, target: str) -> None:
        await self._run("disconnect", target, check=False)

    async def pair(self, host: str, port: int, code: str) -> None:
        """Wireless-debugging pairing. The code is never logged."""
        res = await self._run(
            "pair", _host_port(host, port), code, check=False, timeout=30.0, quiet_args=True
        )
        out = (res.stdout + res.stderr).strip()
        if res.returncode != 0 or "Successfully paired" not in out:
            raise AdbError(f"adb pair failed: {out.replace(code, '******') or 'no output'}")

    async def tcpip(self, serial: str, port: int = LEGACY_TCP_PORT) -> None:
        """Restart adbd on the tablet in legacy TCP mode (used for tablet-hotspot mode)."""
        res = await self._run("tcpip", str(port), serial=serial)
        if "restarting" not in res.stdout.lower():
            raise AdbError(f"adb tcpip: {(res.stdout + res.stderr).strip() or 'no output'}")

    async def forward(self, serial: str, local: int, remote: int) -> None:
        """Laptop port `local` reaches tablet port `remote`."""
        await self._run("forward", f"tcp:{local}", f"tcp:{remote}", serial=serial)

    async def forward_remove(self, serial: str, local: int) -> None:
        await self._run("forward", "--remove", f"tcp:{local}", serial=serial, check=False)

    async def reverse(self, serial: str, remote: int, local: int) -> None:
        """Tablet port `remote` (on 127.0.0.1) reaches laptop port `local`."""
        await self._run("reverse", f"tcp:{remote}", f"tcp:{local}", serial=serial)

    async def reverse_remove(self, serial: str, remote: int) -> None:
        await self._run("reverse", "--remove", f"tcp:{remote}", serial=serial, check=False)

    async def reverse_list(self, serial: str) -> list[tuple[int, int]]:
        res = await self._run("reverse", "--list", serial=serial)
        pairs = []
        for m in re.finditer(r"tcp:(\d+)\s+tcp:(\d+)", res.stdout):
            pairs.append((int(m.group(1)), int(m.group(2))))
        return pairs

    async def shell(self, serial: str, *cmd: str, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        return (await self._run("shell", *cmd, serial=serial, timeout=timeout)).stdout

    async def install(self, serial: str, apk: Path) -> None:
        await self._run("install", "-r", str(apk), serial=serial, timeout=120.0)

    async def wifi_addrs(self, serial: str) -> list[str]:
        """The tablet's IPv4 addresses on its Wi-Fi interfaces (client or hotspot)."""
        out = await self.shell(serial, "ip", "-o", "-4", "addr", "show")
        addrs = []
        for line in out.splitlines():
            iface = line.split()[1] if len(line.split()) > 1 else ""
            if iface.startswith(("wlan", "ap", "swlan")):
                addrs += parse_ipv4_addrs(line)
        return addrs

    async def getprop(self, serial: str, name: str) -> str:
        return (await self.shell(serial, "getprop", name)).strip()
