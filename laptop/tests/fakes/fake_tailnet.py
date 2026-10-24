"""A fake tablink-tailnet helper, speaking the same JSON lines."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeHelper:
    state: str = "Running"
    peers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "host": "tablink-2505drp06g",
                "os": "android",
                "online": True,
                "ips": ["100.64.0.7", "fd7a:115c:a1e0::7"],
            },
            {"host": "work-pc", "os": "windows", "online": True, "ips": ["100.64.0.9"]},
        ]
    )
    init: dict[str, Any] | None = None
    forwards: dict[str, str] = field(default_factory=dict)
    out: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    closed: bool = False

    async def write_line(self, line: str) -> None:
        msg = json.loads(line)
        cmd = msg["cmd"]
        if cmd == "init":
            self.init = msg
            if not msg["state"]:  # first start: Tailscale makes a machine key
                key = base64.b64encode(b"machine-secret").decode()
                await self.out.put(json.dumps({"type": "state", "key": "_machinekey", "data": key}))
        elif cmd == "status":
            url = "https://login.tailscale.com/a/x" if self.state == "NeedsLogin" else ""
            reply = {
                "type": "status",
                "state": self.state,
                "ips": ["100.64.0.1"],
                "peers": self.peers,
                "auth_url": url,
            }
            await self.out.put(json.dumps(reply))
        elif cmd == "forward":
            self.forwards[msg["listen"]] = msg["to"]
            await self.out.put(json.dumps({"type": "forward", "ok": True}))
        elif cmd == "login":
            await self.out.put(json.dumps({"type": "login", "ok": True}))
        elif cmd == "logout":
            self.state = "NeedsLogin"
            await self.out.put(json.dumps({"type": "logout", "ok": True}))

    async def read_line(self) -> str | None:
        if self.closed:
            return None
        return await self.out.get()

    async def close(self) -> None:
        self.closed = True
        await self.out.put("")
