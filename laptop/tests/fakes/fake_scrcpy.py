"""A fake process spawner for scrcpy (and any other long-running tool)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


class FakeProcess:
    def __init__(self, argv: Sequence[str], env: Mapping[str, str] | None) -> None:
        self.argv = list(argv)
        self.env = dict(env or {})
        self._code: int | None = None
        self._done = asyncio.Event()
        self.output: list[str] = []
        self.terminated = False

    @property
    def returncode(self) -> int | None:
        return self._code

    def exit(self, code: int, *lines: str) -> None:
        """Simulate the program exiting on its own (window closed, device lost, ...)."""
        self.output += lines
        self._code = code
        self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        assert self._code is not None
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        if self._code is None:
            self.exit(-15)

    def kill(self) -> None:
        self.terminate()

    def output_tail(self) -> list[str]:
        return self.output[-40:]


@dataclass
class FakeSpawner:
    procs: list[FakeProcess] = field(default_factory=list)
    exit_immediately: tuple[int, tuple[str, ...]] | None = None

    async def __call__(self, argv: Sequence[str], env: Mapping[str, str] | None) -> FakeProcess:
        p = FakeProcess(argv, env)
        self.procs.append(p)
        if self.exit_immediately is not None:
            code, lines = self.exit_immediately
            p.exit(code, *lines)
        return p

    @property
    def last(self) -> FakeProcess:
        return self.procs[-1]

    @property
    def live(self) -> list[FakeProcess]:
        return [p for p in self.procs if p.returncode is None]
