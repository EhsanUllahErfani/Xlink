"""Run one external program. Used only by the tool wrapper modules (adb.py, services/, paths/).

Wrappers take a `Runner` so tests replace real programs with fakes from tests/fakes/.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], Awaitable[RunResult]]


async def subprocess_runner(argv: Sequence[str], timeout: float) -> RunResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return RunResult(
        proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    )
