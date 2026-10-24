"""The `tablink` command."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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

    # Normally the daemon owns the window, so it can follow the cable/Wi-Fi switch.
    try:
        reply = asyncio.run(daemon.request({"cmd": "open_screen"}))
    except (OSError, TimeoutError, ValueError):
        reply = None
    if isinstance(reply, dict) and "message" in reply:
        msg = str(reply["message"])
        if not msg.startswith(("Opening", "The tablet window is already")):
            desktop_notify("TabLink", msg, urgent=True)
            return launcher.NO_TABLET
        return launcher.OK

    with launcher.single_instance() as first:
        if not first:
            desktop_notify("TabLink", "The tablet window is already open.")
            return launcher.ALREADY_OPEN
        try:
            return asyncio.run(launcher.connect(desktop_notify))
        except KeyboardInterrupt:
            return 130


async def active_serial(adb: object) -> tuple[str | None, str | None]:
    """(adb serial, mode) of the link the daemon is using; USB if the daemon is not running."""
    st = await daemon.get_status()
    if st.running and st.serial:
        return st.serial, st.mode
    dev = await adb.usb_device()  # type: ignore[attr-defined]
    return (dev.serial, "usb") if dev else (None, None)


NO_TABLET_TEXT = (
    "Plug in the USB cable, or put both devices on the same Wi-Fi, hotspot or Tailscale."
)


def cmd_remote(args: argparse.Namespace) -> int:
    from tablink.adb import Adb, AdbError
    from tablink.notify import desktop_notify
    from tablink.remote_session import show_laptop_on_tablet
    from tablink.services.remote_desktop import RemoteDesktopError

    async def run() -> int:
        adb = Adb()
        serial, _ = await active_serial(adb)
        if serial is None:
            desktop_notify("Tablet not found", NO_TABLET_TEXT, True)
            return 1
        try:
            msg = await show_laptop_on_tablet(adb, serial)
        except (AdbError, RemoteDesktopError) as e:
            desktop_notify("Could not show the laptop on the tablet", str(e), True)
            return 1
        desktop_notify("TabLink", msg)
        return 0

    return asyncio.run(run())


def cmd_pair(args: argparse.Namespace) -> int:
    from tablink.adb import Adb, AdbError
    from tablink.pairing import PairError, Pairing
    from tablink.services.remote_desktop import RemoteDesktopError

    s = Style(sys.stdout)
    marks = {
        "run": s.dim("…"),
        "ok": s.ok("✓"),
        "skip": s.dim("✓"),
        "wait": s.warn("!"),
        "fail": s.bad("✗"),
    }

    def progress(state: str, text: str) -> None:
        print(f"  {marks.get(state, ' ')} {text}", flush=True)

    print(s.bold("TabLink setup") + s.dim("  (safe to run again; finished steps are skipped)"))
    try:
        asyncio.run(Pairing(Adb(), progress).run())
    except PairError as e:
        progress("fail", str(e))
        return 1
    except (AdbError, RemoteDesktopError, OSError) as e:
        progress("fail", f"{e}. Run `tablink pair` again; finished steps are skipped.")
        return 1
    return 0


def cmd_hotspot(args: argparse.Namespace) -> int:
    if args.state == "password":
        from tablink.secrets import KeyringStore

        pw = KeyringStore("hotspot-password").get()
        if pw is None:
            print("No hotspot password yet: run `tablink hotspot on` first.", file=sys.stderr)
            return 1
        ssid = config.load().network.hotspot_ssid
        print(f"Wi-Fi name: {ssid}\nPassword:  {pw}")  # shown only when asked for
        return 0
    try:
        # Starting the hotspot can take NetworkManager a while.
        reply = asyncio.run(daemon.request({"cmd": f"hotspot_{args.state}"}, timeout=60.0))
    except (OSError, TimeoutError, ValueError):
        print("tablink: the TabLink service is not running (systemctl --user start tablinkd)",
              file=sys.stderr)  # fmt: skip
        return 1
    print(reply.get("message", reply))
    return 0 if reply.get("ok") else 1


def cmd_send(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tablink.adb import Adb, AdbError
    from tablink.services.scrcpy import TABLET_DROP_DIR

    files = [Path(f) for f in args.files]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        print(f"tablink: not a file: {', '.join(missing)}", file=sys.stderr)
        return 1

    async def run() -> int:
        adb = Adb()
        serial, _mode = await active_serial(adb)
        if serial is None:
            print(f"tablink: tablet not found. {NO_TABLET_TEXT}", file=sys.stderr)
            return 1
        for f in files:
            try:
                await adb.push(serial, f, TABLET_DROP_DIR)
            except AdbError as e:
                print(f"tablink: could not send {f.name}: {e}", file=sys.stderr)
                return 1
            print(f"sent {f.name}")
        print(f"{len(files)} file(s) in Download/TabLink on the tablet")
        return 0

    return asyncio.run(run())


def cmd_doctor(args: argparse.Namespace) -> int:
    from tablink.doctor import Doctor

    s = Style(sys.stdout)
    checks = asyncio.run(Doctor().run())
    print(s.bold("TabLink doctor"))
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = s.ok("✓") if c.ok else (s.dim("·") if c.ok is None else s.bad("✗"))
        print(f"  {mark} {c.name:<{width}}  {c.detail}")
        if c.fix and c.ok is False:
            print(s.dim(f"    {'':<{width}}  fix: {c.fix}"))
    return 0 if all(c.ok is not False for c in checks) else 1


def cmd_internet(args: argparse.Namespace) -> int:
    try:
        reply = asyncio.run(daemon.request({"cmd": "internet", "action": args.state}, timeout=45.0))
    except (OSError, TimeoutError, ValueError):
        print("tablink: the TabLink service is not running", file=sys.stderr)
        return 1
    print(reply.get("message", reply))
    if url := reply.get("url"):
        print(f"  {url}")
        with contextlib.suppress(Exception):
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(url, None)  # opens the browser
    return 0 if reply.get("ok") else 1


def cmd_path(args: argparse.Namespace) -> int:
    if not args.force and not args.auto:
        print("use --force MODE or --auto", file=sys.stderr)
        return 2
    mode = None if args.auto else args.force
    try:
        reply = asyncio.run(daemon.request({"cmd": "force_path", "mode": mode}, timeout=30.0))
    except (OSError, TimeoutError, ValueError):
        print("tablink: the TabLink service is not running", file=sys.stderr)
        return 1
    print(reply.get("message", reply))
    return 0 if reply.get("ok") else 1


def cmd_wake(args: argparse.Namespace) -> int:
    from tablink.adb import Adb, AdbError
    from tablink.notify import desktop_notify  # also prints, for terminal users

    async def run() -> int:
        adb = Adb()
        serial, mode = await active_serial(adb)
        if serial is None:
            desktop_notify("Tablet not reachable", NO_TABLET_TEXT, True)
            return 1
        try:
            state = await adb.wake_and_show_unlock(serial)
        except AdbError as e:
            desktop_notify("Could not wake the tablet", str(e), True)
            return 1
        via = f" (via {MODE_LABELS.get(mode or '', mode)})" if mode else ""
        if state == "unlocked":
            desktop_notify("Tablet is awake", f"Unlocked and ready{via}.")
            return 0
        # Locked: open the tablet window; its unlock pad pops up for the pattern or PIN.
        with contextlib.suppress(OSError, TimeoutError, ValueError):
            await daemon.request({"cmd": "open_screen"})
        desktop_notify("Tablet is awake", f"Draw your pattern on the unlock pad{via}.")
        return 0

    return asyncio.run(run())


def cmd_bar(args: argparse.Namespace) -> int:
    from tablink.ui import bar

    return bar.run(args.serial, args.name, args.subtitle)


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
    sub.add_parser("remote", help="show this laptop's desktop on the tablet")
    sub.add_parser("wake", help="wake the tablet and show its PIN pad")
    hs = sub.add_parser("hotspot", help="start or stop the laptop hotspot")
    hs.add_argument("state", choices=("on", "off", "password"))
    send = sub.add_parser("send", help="send files to the tablet")
    send.add_argument("files", nargs="+", metavar="FILE")
    sub.add_parser("doctor", help="test every link and print a fix for each failure")
    net = sub.add_parser("internet", help="built-in Tailscale: reach the tablet from anywhere")
    net.add_argument("state", choices=("on", "off", "status"))
    path = sub.add_parser("path", help="pin one link mode, for testing")
    path.add_argument("--force", choices=MODES, metavar="MODE", help=", ".join(MODES))
    path.add_argument("--auto", action="store_true", help="go back to automatic link choice")
    sub.add_parser("daemon", help="run tablinkd in the foreground")
    b = sub.add_parser("bar", help=argparse.SUPPRESS)  # started by `connect`
    b.add_argument("--serial", required=True)
    b.add_argument("--name", default="Tablet")
    b.add_argument("--subtitle", default="")

    p.set_defaults(func=None)
    return p


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "status": cmd_status,
    "connect": cmd_connect,
    "bar": cmd_bar,
    "remote": cmd_remote,
    "wake": cmd_wake,
    "pair": cmd_pair,
    "hotspot": cmd_hotspot,
    "send": cmd_send,
    "doctor": cmd_doctor,
    "path": cmd_path,
    "internet": cmd_internet,
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
