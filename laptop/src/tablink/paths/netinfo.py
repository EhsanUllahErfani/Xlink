"""Facts about this laptop's network, read without running any program."""

from __future__ import annotations

import socket
from pathlib import Path


def default_gateways(route_file: Path = Path("/proc/net/route")) -> list[str]:
    """IPv4 default gateways, best (lowest metric) first."""
    gws: list[tuple[int, str]] = []
    try:
        lines = route_file.read_text().splitlines()[1:]
    except OSError:
        return []
    for line in lines:
        f = line.split()
        if len(f) < 8 or f[1] != "00000000" or f[2] == "00000000":
            continue
        gw = socket.inet_ntoa(int(f[2], 16).to_bytes(4, "little"))
        gws.append((int(f[6]), gw))
    return [g for _, g in sorted(gws)]


def local_addr_towards(addr: str) -> str | None:
    """This laptop's address on the route to `addr` (no packet is sent)."""
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((addr, 9))
            local: str = s.getsockname()[0]
            return local
    except OSError:
        return None


def neighbours(iface: str, arp_file: Path = Path("/proc/net/arp")) -> list[str]:
    """IPv4 neighbours this laptop has talked to on `iface` (e.g. hotspot clients)."""
    try:
        lines = arp_file.read_text().splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in lines:
        f = line.split()
        # IP address, HW type, Flags, HW address, Mask, Device; flag 0x2 = complete
        if len(f) >= 6 and f[5] == iface and int(f[2], 16) & 0x2:
            out.append(f[0])
    return out
