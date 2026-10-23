# TabLink

TabLink links one Ubuntu laptop with one Redmi Pad 2 tablet. One click on either device takes control
of the other, with shared clipboard, file transfer, a synced folder, and compute offload, over any
link the two devices share: USB, router Wi-Fi/Ethernet, laptop hotspot, tablet hotspot, a third
phone's hotspot, Bluetooth, or the internet (Tailscale).

The full specification is `docs/SPEC.md`. Read it before starting any task. If this file and the
spec disagree, the spec wins; point out the conflict.

## Target devices

- Laptop: Ubuntu (GNOME on Wayland), x86_64, normal user with sudo.
- Tablet: Redmi Pad 2 (codename taiko), HyperOS 3 / Android 16, MediaTek MT6789. NO root, NO
  bootloader unlock. Ever.

## Stack (ask before adding anything not listed)

- Laptop: Python 3.12, asyncio, PyGObject (GTK4 + libadwaita), python-zeroconf, websockets,
  secretstorage, tomli-w. Tests: pytest, pytest-asyncio. Lint/type: ruff, mypy.
- Android: Kotlin, Jetpack Compose, OkHttp (WebSocket), minSdk 30, targetSdk 36. Tests: JUnit.
- External tools wrapped (one module each): adb, scrcpy, nmcli, tailscale, syncthing (REST API),
  rustdesk, gsconnect/kdeconnect, ssh.

## Repo layout

```
laptop/src/tablink/   cli.py daemon.py config.py adb.py control/ paths/ services/ pairing.py ui/
laptop/tests/         pytest; fakes for every external tool live in tests/fakes/
laptop/data/          tablink.desktop, tablinkd.service, icons
android/app/src/main/java/dev/tablink/
scripts/install.sh
```

## Commands

```bash
# laptop
cd laptop && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest -q
ruff check . && ruff format --check . && mypy src
tablink status            # once the CLI exists

# android
cd android && ./gradlew assembleDebug test
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## How to work

1. Do ONE task from the spec's task list (T1 to T19) at a time, in order.
2. A task is done only when its "Check" passes. Run tests and lint before saying it's done.
3. If a check needs the real tablet, say exactly what the user must do and what they should see.
4. Stop after each task with a short summary: what changed, how it was checked, what's next.
5. Small commits with clear messages; never commit secrets, keys, or `.venv`.

## Architecture rules

- `PathProvider` (paths/) finds and activates a link; `Service` (services/) reacts to
  `on_path_changed`. The path manager owns switching. Services never pick a path themselves.
- Path priority: usb > lan/hotspots > tailscale > bluetooth. Health check every 5 s.
- Every external tool is called from exactly one wrapper module. No `subprocess` anywhere else.
  Each wrapper has a fake in `tests/fakes/` so all unit tests run with no device attached.
- Never hardcode IPs or hotspot subnets. Discover: saved addresses, mDNS `_tablink._tcp`,
  default gateway, `tailscale status --json`.
- Secrets (hotspot password, pairing keys) go in GNOME Keyring / Android Keystore. Never in
  config files or logs.
- All TabLink traffic uses the control port (default 47800, TLS WebSocket, keys pinned at pairing).

## Ports (USB mode maps each with `adb reverse` so the tablet uses 127.0.0.1)

| Service | Port |
|---|---|
| TabLink control | 47800 |
| RustDesk direct IP | 21118 |
| Syncthing | 22000 |
| KDE Connect | 1716 |
| SSH | 22 |
| code-server | 8080 |

## Platform gotchas (design around these, don't fight them)

- Wireless debugging only works while the tablet is a Wi-Fi CLIENT. In tablet-hotspot mode use
  legacy `adb tcpip 5555`, bootstrapped over USB; it resets on every tablet reboot.
- Android blocks background clipboard reads. Tablet-to-laptop clipboard is automatic only during a
  scrcpy session or while the TabLink app is in the foreground; otherwise use the Quick Settings tile.
- Wayland asks the user to approve screen sharing for new sessions. Show a clear prompt; don't try
  to bypass it.
- HyperOS kills background apps. The Android app must run a foreground service and show a first-run
  checklist (battery "No restrictions", autostart on).
- Tailscale cannot discover peers from a cold start without internet. It is only for mode 7 (and as
  a relay for isolated Wi-Fi), never for local modes.
- Bluetooth PAN is too slow for video: disable screen control there and say so.
- Campus Wi-Fi may block device-to-device traffic. Detect it (same SSID, peer unreachable) and fall
  back automatically.

## Never

- Require root, a bootloader unlock, or disabling Android security features.
- Use public relay servers for local modes.
- Store or log passwords, keys, or clipboard contents.
- Add a dependency without asking.
