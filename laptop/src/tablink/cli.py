"""The `tablink` command."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from typing import TextIO

from tablink import __version__, config, daemon

MODES = ("usb", "lan", "laptop_hotspot", "tablet_hotspot", "tailscale", "bluetooth")

MODE_LABELS = {
    "usb": "USB cable",
    "lan": "same network",
    "laptop_hotspot": "laptop hotspot",
    "tablet_hotspot": "tablet hotspot",
    "tailscale": "Tailscale",
    "bluetooth": "Bluetooth",
}


class Style:
    """ANSI colours, only when writing to a terminal and NO_COLOR is unset."""

    def __init__(self, out: TextIO) -> None:
        self.on = out.isatty() and "NO_COLOR" not in os.environ

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def ok(self, t: str) -> str:
        return self._wrap("32", t)

    def warn(self, t: str) -> str:
        return self._wrap("33", t)

    def bad(self, t: str) -> str:
        return self._wrap("31", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def bold(self, t: str) -> str:
        return self._wrap("1", t)


STATE_STYLE: dict[str, Callable[[Style, str], str]] = {
    "connected": Style.ok,
    "syncing": Style.warn,
    "offline": Style.bad,
    "disabled": Style.dim,
}


def format_status(st: daemon.DaemonStatus, cfg: config.Config, s: Style) -> str:
    lines = [s.bold(f"TabLink {__version__}") + s.dim(f"  ·  {cfg.device.tablet_name}")]
    if not st.running:
        lines.append(f"  daemon   {s.bad('not running')}")
        lines.append(s.dim("           start it with: systemctl --user start tablinkd"))
        return "\n".join(lines)
    if st.mode is None:
        lines.append(f"  link     {s.bad('offline')}")
        lines.append(s.dim("           try: plug in USB, or run `tablink hotspot on`"))
    else:
        rtt = f"  {st.rtt_ms:.0f} ms" if st.rtt_ms is not None else ""
        lines.append(f"  link     {s.ok(MODE_LABELS.get(st.mode, st.mode))}{s.dim(rtt)}")
    if st.services:
        width = max(len(svc.name) for svc in st.services)
        for svc in st.services:
            paint = STATE_STYLE.get(svc.state, Style.dim)
            detail = s.dim(f"  {svc.detail}") if svc.detail else ""
            lines.append(f"  {svc.name:<{width}}  {paint(s, svc.state)}{detail}")
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"tablink: bad config: {e}", file=sys.stderr)
        return 1
    st = asyncio.run(daemon.get_status())
    print(format_status(st, cfg, Style(sys.stdout)))
    return 0 if st.running and st.mode else 3


def cmd_connect(args: argparse.Namespace) -> int:
    from tablink import launcher
    from tablink.notify import desktop_notify

    with launcher.single_instance() as first:
        if not first:
            desktop_notify("TabLink", "The tablet window is already open.")
            return launcher.ALREADY_OPEN
        try:
            return asyncio.run(launcher.connect(desktop_notify))
        except KeyboardInterrupt:
            return 130


def not_yet(task: str) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        print(f"tablink {args.command}: not implemented yet ({task})", file=sys.stderr)
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tablink",
        description="Link this laptop with your tablet. With no command: connect and show the "
        "tablet screen.",
    )
    p.add_argument("--version", action="version", version=f"tablink {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("connect", help="connect on the best path and open the tablet screen (default)")
    sub.add_parser("status", help="active link, delay and the state of each service")
    sub.add_parser("pair", help="first-time setup over USB")
    hs = sub.add_parser("hotspot", help="start or stop the laptop hotspot")
    hs.add_argument("state", choices=("on", "off"))
    send = sub.add_parser("send", help="send files to the tablet")
    send.add_argument("files", nargs="+", metavar="FILE")
    sub.add_parser("doctor", help="test every link and print a fix for each failure")
    path = sub.add_parser("path", help="pin one link mode, for testing")
    path.add_argument("--force", choices=MODES, metavar="MODE", help=", ".join(MODES))
    sub.add_parser("daemon", help="run tablinkd in the foreground")

    p.set_defaults(func=None)
    return p


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "status": cmd_status,
    "connect": cmd_connect,
    "pair": not_yet("T17"),
    "hotspot": not_yet("T7"),
    "send": not_yet("T15"),
    "doctor": not_yet("T18"),
    "path": not_yet("T5"),
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        args.command = "connect"
    if args.command == "daemon":
        daemon.main()
        return 0
    return HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
