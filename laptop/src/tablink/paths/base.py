"""The path interfaces from docs/SPEC.md, "Core interfaces"."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Mode = Literal[
    "usb", "private", "lan", "laptop_hotspot", "tablet_hotspot", "tailscale", "bluetooth"
]

# Lower = preferred. "private" is TabLink's own encrypted network (laptop server), used on any
# Wi-Fi and from far away; Tailscale is its fallback. Plain adb over Wi-Fi (lan/hotspots) is not
# end-to-end encrypted, so it is only a last resort.
PRIORITY: dict[str, int] = {
    "usb": 0,
    "private": 5,
    "tailscale": 20,
    "lan": 25,
    "laptop_hotspot": 26,
    "tablet_hotspot": 27,
    "bluetooth": 30,
}

# Video preset per mode (docs/SPEC.md, "Switching paths"). None = no video on this path.
PRESET_FOR_MODE: dict[str, str | None] = {
    "usb": "high",
    "private": "high",
    "lan": "high",
    "laptop_hotspot": "balanced",
    "tablet_hotspot": "balanced",
    "tailscale": "low",
    "bluetooth": None,
}


@dataclass(frozen=True)
class Path:
    mode: Mode
    tablet_addr: str  # where the laptop reaches the tablet
    laptop_addr: str  # where the tablet reaches the laptop ("127.0.0.1" on USB)
    adb_serial: str | None  # None on Bluetooth
    rtt_ms: float
    video_ok: bool  # False on Bluetooth

    @property
    def preset(self) -> str | None:
        return PRESET_FOR_MODE[self.mode] if self.video_ok else None


class PathProvider(Protocol):
    mode: str
    priority: int  # lower = preferred

    async def probe(self) -> Path | None: ...

    async def activate(self, path: Path) -> None: ...  # adb reverse, adb tcpip, hotspot on

    async def deactivate(self) -> None: ...
