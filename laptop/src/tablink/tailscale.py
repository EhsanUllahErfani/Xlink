"""Tailscale. The only module that runs the tailscale command.

Used for mode 7 (devices on different networks) and as a relay when campus Wi-Fi isolates
devices. Never used for local modes, and never needed to discover a local tablet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tablink.proc import Runner, subprocess_runner


@dataclass(frozen=True)
class Peer:
    hostname: str
    os: str
    ips: tuple[str, ...]
    online: bool


@dataclass(frozen=True)
class TailnetStatus:
    running: bool
    self_ips: tuple[str, ...]
    peers: tuple[Peer, ...]


def parse_status(text: str) -> TailnetStatus:
    try:
        data: dict[str, Any] = json.loads(text)
    except ValueError:
        return TailnetStatus(False, (), ())
    me = data.get("Self") or {}
    peers = []
    for p in (data.get("Peer") or {}).values():
        peers.append(
            Peer(
                hostname=str(p.get("HostName", "")),
                os=str(p.get("OS", "")),
                ips=tuple(p.get("TailscaleIPs") or ()),
                online=bool(p.get("Online")),
            )
        )
    return TailnetStatus(
        running=data.get("BackendState") == "Running",
        self_ips=tuple(me.get("TailscaleIPs") or ()),
        peers=tuple(peers),
    )


def ipv4(ips: tuple[str, ...]) -> str | None:
    return next((ip for ip in ips if ":" not in ip), ips[0] if ips else None)


def find_tablet(status: TailnetStatus, name_hint: str = "") -> Peer | None:
    """The tablet among the peers: the one named like it, else the only online Android."""
    online = [p for p in status.peers if p.online and p.ips]
    if name_hint:
        hint = name_hint.lower().replace(" ", "-")
        named = [p for p in online if hint in p.hostname.lower()]
        if named:
            return named[0]
    android = [p for p in online if p.os.lower() == "android"]
    return android[0] if len(android) == 1 else None


class Tailscale:
    def __init__(self, runner: Runner | None = None) -> None:
        self._run = runner or subprocess_runner

    async def status(self) -> TailnetStatus:
        try:
            res = await self._run(["tailscale", "status", "--json"], 10.0)
        except FileNotFoundError:  # not installed
            return TailnetStatus(False, (), ())
        if res.returncode != 0:
            return TailnetStatus(False, (), ())
        return parse_status(res.stdout)
