"""systemd user units. The only module that runs systemctl."""

from __future__ import annotations

from tablink.proc import Runner, subprocess_runner


class Systemd:
    def __init__(self, runner: Runner | None = None) -> None:
        self._run = runner or subprocess_runner

    async def is_active(self, unit: str) -> bool:
        res = await self._run(["systemctl", "--user", "is-active", unit], 10.0)
        return res.stdout.strip() == "active"

    async def enable_now(self, unit: str) -> None:
        res = await self._run(["systemctl", "--user", "enable", "--now", unit], 30.0)
        if res.returncode != 0:
            raise RuntimeError(f"could not start {unit}: {(res.stderr or res.stdout).strip()}")

    async def restart(self, unit: str) -> None:
        res = await self._run(["systemctl", "--user", "restart", unit], 30.0)
        if res.returncode != 0:
            raise RuntimeError(f"could not restart {unit}: {(res.stderr or res.stdout).strip()}")
