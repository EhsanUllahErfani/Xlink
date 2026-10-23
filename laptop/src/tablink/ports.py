"""TabLink's ports (docs/SPEC.md, "One control port on every path")."""

from __future__ import annotations

from dataclasses import dataclass

CONTROL = 47800


@dataclass(frozen=True)
class PortMap:
    name: str
    laptop: int  # where the service listens on the laptop
    tablet: int  # what the tablet dials on 127.0.0.1 in USB mode (adb reverse)


def usb_reverse_ports(control_port: int = CONTROL) -> list[PortMap]:
    """Laptop services the tablet reaches over the USB tunnel.

    SSH is the one exception to "same port on both ends": Android does not let adbd bind
    ports below 1024, so the tablet dials 127.0.0.1:2222 and reaches the laptop's port 22.
    """
    return [
        PortMap("control", control_port, control_port),
        PortMap("remote-desktop", 3389, 3389),  # GNOME Remote Desktop (RDP), see services/
        PortMap("syncthing", 22000, 22000),
        PortMap("kdeconnect", 1716, 1716),
        PortMap("ssh", 22, 2222),
        PortMap("code-server", 8080, 8080),
    ]
