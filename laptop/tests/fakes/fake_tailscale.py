"""A fake `tailscale status --json`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tablink.proc import RunResult


@dataclass
class FakeTailscale:
    running: bool = True
    installed: bool = True
    peers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "HostName": "redmi-pad-2",
                "OS": "android",
                "Online": True,
                "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"],
            },
            {
                "HostName": "work-pc",
                "OS": "windows",
                "Online": True,
                "TailscaleIPs": ["100.64.0.9"],
            },
        ]
    )

    async def __call__(
        self, argv: Sequence[str], timeout: float, input: bytes | None = None
    ) -> RunResult:
        if not self.installed:
            raise FileNotFoundError("tailscale")
        data = {
            "BackendState": "Running" if self.running else "Stopped",
            "Self": {"HostName": "laptop", "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"]},
            "Peer": {f"k{i}": p for i, p in enumerate(self.peers)},
        }
        return RunResult(0, json.dumps(data), "")
