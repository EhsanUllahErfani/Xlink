"""Pair the tablet over Wi-Fi, no USB: Android's Wireless debugging, by QR code or 6-digit code.

QR: the laptop shows "WIFI:T:ADB;S:<name>;P:<password>;;"; the tablet scans it in Developer
options > Wireless debugging > Pair device with QR code, then announces a pairing service on the
Wi-Fi under <name>; the laptop finds it (adb's own discovery) and pairs with <password>.
Code: the user reads the tablet's "IP address & Port" and 6-digit code into the laptop.

After pairing, adb is switched to legacy TCP mode (port 5555), which keeps working across
network changes until the tablet restarts; TabLink then reaches it through its encrypted network.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import secrets
import string
from dataclasses import dataclass

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError

ALPHABET = string.ascii_letters + string.digits


class WifiPairError(RuntimeError):
    pass


@dataclass(frozen=True)
class QrPairing:
    name: str
    password: str  # secret, single use

    @property
    def payload(self) -> str:
        return f"WIFI:T:ADB;S:{self.name};P:{self.password};;"

    def png(self, scale: int = 8) -> bytes:
        import segno

        buf = io.BytesIO()
        segno.make(self.payload, error="m").save(buf, kind="png", scale=scale, border=2)
        return buf.getvalue()

    def terminal(self) -> str:
        import segno

        buf = io.StringIO()
        segno.make(self.payload, error="m").terminal(out=buf, compact=True, border=2)
        return buf.getvalue()


def new_qr() -> QrPairing:
    rand = "".join(secrets.choice(ALPHABET) for _ in range(6))
    pw = "".join(secrets.choice(ALPHABET) for _ in range(10))
    return QrPairing(name=f"studio-tablink-{rand}", password=pw)


async def wait_and_pair_qr(adb: Adb, qr: QrPairing, timeout: float = 180.0) -> str:
    """Wait until the tablet scanned the QR and pair with it. Returns the tablet's IP."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        for name, kind, addr in await adb.mdns_services():
            if kind == "_adb-tls-pairing._tcp" and name == qr.name:
                host, _, port = addr.rpartition(":")
                await adb.pair(host, int(port), qr.password)
                return host
        await asyncio.sleep(1.0)
    raise WifiPairError(
        "The tablet did not show up. Check both are on the same Wi-Fi, or use the "
        "6-digit pairing code instead."
    )


async def pair_with_code(adb: Adb, host: str, port: int, code: str) -> str:
    if not (code.isdigit() and len(code) == 6):
        raise WifiPairError("The pairing code is the 6 digits shown on the tablet.")
    await adb.pair(host, port, code)
    return host


async def connect_after_pairing(
    adb: Adb, host: str, connect_port: int | None = None, timeout: float = 30.0
) -> str:
    """Connect to the paired tablet and switch it to adb over TCP 5555. Returns the serial."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    serial = None
    while serial is None and loop.time() < end:
        if connect_port:
            with contextlib.suppress(AdbError):
                serial = await adb.connect(host, connect_port)
        else:
            for _name, kind, addr in await adb.mdns_services():
                if kind == "_adb-tls-connect._tcp" and addr.rpartition(":")[0] == host:
                    h, _, p = addr.rpartition(":")
                    try:
                        serial = await adb.connect(h, int(p))
                    except AdbError:
                        continue
                    break
        if serial is None:
            await asyncio.sleep(1.0)
    if serial is None:
        raise WifiPairError(
            "Paired, but could not connect. On the tablet, keep Wireless debugging on and "
            'enter the "IP address & Port" it shows.'
        )
    await adb.tcpip(serial, LEGACY_TCP_PORT)  # survives Wi-Fi changes until the next reboot
    await asyncio.sleep(2.0)
    return await adb.connect(host, LEGACY_TCP_PORT)
