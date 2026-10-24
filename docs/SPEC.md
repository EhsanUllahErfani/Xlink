# TabLink — Laptop ↔ Redmi Pad 2 App Specification

Oct 23, 2026

## Overview

TabLink pairs one Ubuntu laptop with one Redmi Pad 2 so that a single tap on either device takes
control of the other, with a shared clipboard, shared folders, and file drop in both directions.

### The one-tap goal

- **On the laptop:** one TabLink icon. Click it and the tablet's screen opens in a window, fully
  controllable with the laptop's keyboard and mouse.
- **On the tablet:** one TabLink app icon. Tap it and the laptop's desktop opens full screen, fully
  controllable by touch, stylus or a Bluetooth keyboard.
- Either way, copy on one device and paste on the other, and drop a file on one to land it on the
  other.

### What "sharing everything" means here

| Shared | How | Realistic? |
|---|---|---|
| Screen and input (both directions) | Screen streaming + remote input | Yes |
| Clipboard | Sync on every copy | Yes |
| Files and folders | Two-way folder sync + one-off send | Yes |
| Notifications | Tablet notifications shown on laptop | Yes |
| Audio | Tablet audio played through laptop | Yes (laptop to tablet is harder) |
| Compute (CPU / GPU / RAM) | Heavy jobs sent to the laptop and run there | Partly: jobs can be offloaded, but two devices never become one merged computer |
| Live co-editing of one file | Shared folder + editor that reloads on change | Partly: one editor at a time per file |

The compute row is the key limit. No practical system pools a laptop's and a tablet's CPU and RAM
into one machine. TabLink instead lets the tablet use the laptop's power: run code, training jobs or
builds on the laptop and see the result on the tablet.

## Devices and environment

TabLink v1 targets exactly two devices on the same Wi-Fi or USB cable, with no root and no
bootloader unlock.

| Item | Laptop | Tablet |
|---|---|---|
| Device | User's laptop (x86_64) | Redmi Pad 2 (codename taiko) |
| OS | Ubuntu, GNOME desktop on Wayland | HyperOS 3, Android 16 |
| Chip | Intel or AMD x86_64 | MediaTek Helio G100-Ultra (MT6789), Mali-G57 |
| Privileges | Normal user + sudo | No root; USB debugging / wireless debugging on |
| Already installed | Nothing yet | Termux + Debian (proot) from the earlier setup, optional |

**Network:** TabLink works over every link the two devices can share: USB cable, the same Wi-Fi or
Ethernet network, either device's hotspot, a third phone's hotspot, Bluetooth, and the internet. See
Connection modes.

**Open question:** confirm the laptop is on Ubuntu 24.04 LTS or 26.04 LTS, since Wayland
screen-sharing behaviour differs between them.

## Connection modes

TabLink treats every link between the two devices as a "path" and always uses the best one that
works, so the user never picks a mode by hand.

| Mode | How the devices link | Speed | Screen control (F1, F2) | Clipboard, files, sync | One-time setup |
|---|---|---|---|---|---|
| 1. USB-C cable | ADB over USB; every service tunnelled to 127.0.0.1 with `adb forward` / `adb reverse`, no IP network needed | Fastest, lowest delay | Yes | Yes | Allow USB debugging |
| 2. Same router (Wi-Fi or Ethernet) | Direct IP on the shared network | Good | Yes | Yes | Wireless debugging pairing |
| 3. Laptop hotspot | Laptop runs a hotspot (`nmcli device wifi hotspot`); tablet joins; laptop is the gateway | Good | Yes | Yes | One click "Start hotspot"; laptop Wi-Fi card must support AP mode |
| 4. Tablet hotspot | Laptop joins the tablet's hotspot; tablet is the gateway. Wireless debugging needs the tablet to be a Wi-Fi client, so this mode uses legacy ADB TCP (`adb tcpip 5555`) instead | Good | Yes | Yes | Plug in USB once after each tablet reboot |
| 5. Third phone's hotspot | Both devices join a phone's hotspot; same as mode 2 | Good, shares the phone's bandwidth | Yes | Yes | Same as mode 2 |
| 6. Isolated Wi-Fi (campus) | Network blocks device-to-device traffic; TabLink falls back to modes 1 or 3, or to mode 7's relay | Fair over relay | Low preset | Yes | None extra |
| 7. Internet, different networks | Tailscale (or self-hosted Headscale) WireGuard mesh; fixed tailnet addresses | Depends on both connections | Low preset | Yes | Tailscale sign-in on both devices; needs internet |
| 8. Bluetooth tethering | Tablet shares a network over Bluetooth (PAN); laptop gets an IP | Slow | No, too slow for video | Clipboard, notifications, SSH, small files | Bluetooth pairing |

Wi-Fi Direct is not in v1: Ubuntu and Android app support for it is limited, and mode 3 gives the
same "no router needed" result.

### How TabLink picks a path

1. USB cable attached (ADB sees the tablet over USB) → mode 1.
2. Direct IP reachable → modes 2 to 5. The laptop finds the tablet by, in order: last-known
   addresses, mDNS service `_tablink._tcp`, and the default gateway (if the gateway is the tablet or
   laptop, it is a hotspot mode). A path counts only after the TabLink control handshake succeeds.
3. Both devices on Tailscale → mode 7 (also rescues mode 6).
4. Bluetooth PAN link up → mode 8, with screen control disabled.
5. Nothing works → the launcher offers two buttons: "Start laptop hotspot" and "Plug in USB".

### Switching paths

- A health check runs every 5 seconds on the active path and probes better ones in the background.
- When a better path appears (for example, a cable is plugged in), TabLink switches; a screen
  session restarts within 3 seconds, while clipboard and sync continue without a break.
- Video presets follow the path: High on USB and LAN, Balanced on hotspots, Low on relay or
  internet.

### One control port on every path

The laptop daemon listens on one TabLink control port (default 47800, TLS with keys pinned at
pairing). Every path exposes it the same way: direct IP, tailnet IP, or `adb reverse tcp:47800` on
USB. The tablet app always talks to this port, so it never needs to know which mode is active.

| Service | Default port | USB mode mapping |
|---|---|---|
| TabLink control | 47800 | adb reverse (tablet to laptop) |
| RustDesk direct IP | 21118 | adb reverse |
| Syncthing | 22000 | adb reverse |
| KDE Connect | 1716 | adb reverse |
| SSH | 22 | adb reverse |
| code-server | 8080 | adb reverse |
| scrcpy | ADB tunnel | Built in |

## User stories and the one-click flow

Every story starts with one click or tap and needs no typing of IPs, codes or commands after the
first pairing.

### User stories

1. As the user at my laptop, I click the TabLink icon and the tablet's screen appears in a window
   that I control with my mouse and keyboard.
2. As the user holding the tablet, I tap the TabLink app and my laptop's desktop fills the tablet
   screen; I control it by touch, stylus or Bluetooth keyboard.
3. I copy text on either device and paste it on the other.
4. I drag a file onto the tablet window on the laptop and it lands in the tablet's Downloads; I
   share a file from any tablet app to "TabLink" and it lands on the laptop.
5. A folder called TabLink exists on both devices and always holds the same files.
6. From the tablet, I run a Python script or training job on the laptop and watch its output.
7. Tablet notifications show on the laptop while I work there.
8. I can use the tablet as an extra screen beside the laptop (v2).

### One-click flow: laptop controls tablet

1. Click the TabLink icon in the Ubuntu dock.
2. The launcher checks the tablet is reachable (Wi-Fi first, then USB).
3. It starts the screen session; the tablet window opens within about 3 seconds.
4. Closing the window ends the session; sync and clipboard keep running in the background.

### One-click flow: tablet controls laptop

1. Tap the TabLink app on the tablet.
2. The app finds the paired laptop on the network.
3. It opens the laptop's desktop full screen.
4. Back gesture or the TabLink bar's "Disconnect" ends the session.

## Functional requirements

Ten features make up TabLink; F1 to F8 and F10 are v1, and the IDs are used in the build plan and
tests.

| ID | Feature | Requirement | Priority |
|---|---|---|---|
| F1 | Laptop controls tablet | Live tablet screen in a laptop window; mouse, keyboard, scroll and multi-touch gestures forwarded; under 100 ms latency on Wi-Fi | Must |
| F2 | Tablet controls laptop | Laptop desktop full screen on the tablet; touch acts as trackpad or direct pointer; on-screen and Bluetooth keyboard; right-click and scroll | Must |
| F3 | Clipboard | Laptop to tablet automatic; tablet to laptop automatic while a screen session is open, otherwise one tap on a Quick Settings tile | Must |
| F4 | File send | Drag and drop from laptop onto the tablet window; Android share sheet "Send to laptop"; files land in Downloads/TabLink | Must |
| F5 | Shared folder | ~/TabLink on the laptop and Documents/TabLink on the tablet kept identical in both directions; conflicts kept as copies, never overwritten | Must |
| F6 | Compute offload | From the tablet: open a terminal on the laptop, run a script, see output; optional laptop-hosted VS Code in the tablet browser | Should |
| F7 | Notifications | Tablet notifications mirrored to the laptop; reply to messages from the laptop | Should |
| F8 | Audio | Tablet audio played on the laptop during F1 | Should |
| F9 | Tablet as second screen | Extend the laptop desktop onto the tablet | Could (v2) |
| F10 | One-click launchers | One .desktop icon on Ubuntu, one app icon on the tablet; status (connected, syncing, offline) visible on both | Must |

### Editing the same file on both devices (F5 detail)

A file saved on one device appears on the other within about 5 seconds on the same Wi-Fi. If both
devices edit the same file before sync, the sync keeps both versions and marks one as a conflict
copy, so no work is lost. True simultaneous co-editing of one file is out of scope; use F2 (edit on
the laptop from the tablet) when that's needed.

## Architecture

TabLink is a thin launcher on each device that starts and supervises five proven open-source links;
only the two launchers are new code.

Arrows point from the controlling device to the controlled one; two-headed links sync both ways.
*(The architecture diagram from the original document was not included in this export.)*

### Building blocks

- **scrcpy 4.x** (Apache 2.0): screen, input, audio, clipboard and drag-and-drop file transfer to
  Android over USB or Wi-Fi; version 4.1 (July 2026) added a media scan after file drops so tablet
  apps see new files at once (Forest Watch).
- **RustDesk** (AGPL): the tablet's view of the laptop; supports Wayland and runs direct-IP without
  public servers.
- **GSConnect + KDE Connect** (GPL): clipboard, file share, notifications, remote file browsing.
- **Syncthing / Syncthing-Fork** (MPL): the shared TabLink folder.
- **OpenSSH + code-server**: compute offload (F6).

### New code

- **Laptop launcher:** Python + GTK4, one .desktop file and a tray icon; runs `adb connect` then
  scrcpy with saved presets, and watches the other services.
- **Tablet app:** Kotlin; one button that opens the RustDesk session to the saved laptop address,
  plus a status screen and a Quick Settings tile for clipboard send.

## Connection, pairing and security

Pairing happens once, over a USB cable, and after that every link is encrypted and limited to the
two paired devices on the local network.

### First-time pairing (one setup wizard on the laptop)

1. Plug the tablet into the laptop by USB-C and accept the "Allow USB debugging" prompt.
2. The wizard enables wireless debugging pairing, so F1 works over Wi-Fi without the cable.
3. It exchanges Syncthing device IDs (F5) and KDE Connect pairing (F3, F4, F7) automatically.
4. It installs the remote-desktop client on the tablet and saves the laptop's address and a long
   random password (F2).
5. It writes the TabLink icon to the Ubuntu app menu and stores everything in `~/.config/tablink/`.

### Security rules

- Every channel is encrypted: ADB wireless debugging (TLS after pairing), RustDesk (end-to-end
  encrypted), Syncthing (TLS), KDE Connect (TLS).
- No public relay servers: local modes use direct IP or the USB tunnel, and internet mode (7) runs
  only inside the user's own WireGuard mesh (Tailscale or Headscale). The Ubuntu firewall (ufw)
  allows TabLink ports only from the tablet's address.
- Wireless debugging gives full control of the tablet, so TabLink turns it off when the tablet joins
  an unknown Wi-Fi network.
- On Wayland, Ubuntu asks for permission to share the screen at each new session; the user approves
  on the laptop (see limitations).
- Unpairing from either side revokes all keys at once.

## Platform limitations and risks

The two hardest limits come from the operating systems, not from TabLink: Android blocks background
clipboard reads, and Wayland asks for consent before screen sharing.

| Risk | Cause | Impact | Mitigation |
|---|---|---|---|
| Tablet-to-laptop clipboard not automatic | Since Android 10, background apps can't read the clipboard; KDE Connect users report it needs a manual "Send Clipboard" tap (KDE forum) | F3 needs one tap when no screen session is open | During F1, scrcpy syncs both ways; otherwise a Quick Settings tile |
| Screen-share consent on Wayland | Wayland design asks the user to approve each new session (RustDesk Linux guide) | F2 needs one click on the laptop the first time per session | RustDesk's Wayland unattended-access preview for Debian/Ubuntu x86_64 (AlternativeTo), or GNOME's built-in Remote Desktop (RDP) |
| HyperOS kills background services | Aggressive battery management | Sync and clipboard stop silently | "No restrictions" battery setting, autostart allowed, app locked in recents |
| Wireless debugging turns off | Android disables it on network change or reboot | F1 over Wi-Fi fails until re-enabled | USB fallback; launcher shows a clear "plug in once" message |
| Campus Wi-Fi blocks device-to-device traffic | Client isolation on shared networks | Devices can't see each other | Automatic fallback to USB, laptop hotspot or Tailscale relay (modes 1, 3, 7) |
| Battery drain on the tablet | Video encoding during sessions | Roughly hours, not a full day, of continuous control | Lower resolution and frame rate presets; charge over USB mode |
| Compute is not pooled | Two separate computers | Tablet apps can't use laptop CPU or RAM directly | F6: run heavy work on the laptop, view it on the tablet |

## Build plan

Phase 0 needs no code at all: proving each link by hand first means the launcher only automates
steps already known to work.

1. *(empty in the original document)*
2. Export this document as Markdown to `docs/SPEC.md`, and put the provided `CLAUDE.md` in the repo
   root.
3. Run `claude` in the repo and say: "Read CLAUDE.md and docs/SPEC.md, then do T1. Stop when its
   check passes."
4. Review, commit, then ask for the next task.

### Repo layout

```
tablink/
  CLAUDE.md
  docs/SPEC.md                  export of this document
  laptop/                       Python 3.12 package "tablink"
    pyproject.toml
    src/tablink/
      cli.py                    the `tablink` command
      daemon.py                 tablinkd (asyncio, systemd user service)
      config.py
      adb.py                    async wrapper around adb
      control/                  control channel server, port 47800
      paths/                    usb.py lan.py hotspot.py tailscale.py bluetooth.py manager.py
      services/                 scrcpy.py rustdesk.py syncthing.py kdeconnect.py ssh.py
      pairing.py
      ui/                       tray.py wizard.py (GTK4 + libadwaita)
    data/                       tablink.desktop, tablinkd.service, icons
    tests/                      pytest, with fakes for every external tool
  android/                      Kotlin + Jetpack Compose (Gradle)
    app/src/main/java/dev/tablink/
      MainActivity.kt           one Connect button + status
      LinkService.kt            foreground service
      ControlClient.kt          talks to port 47800
      Discovery.kt              mDNS, gateway, saved addresses
      ClipboardTile.kt          Quick Settings tile
      ShareActivity.kt          "Send to laptop" share target
  scripts/install.sh
  .github/workflows/ci.yml
```

### Stack

| Part | Choice |
|---|---|
| Laptop code | Python 3.12, asyncio; python-zeroconf for mDNS, websockets for the control channel |
| Laptop UI | GTK4 + libadwaita through PyGObject; tray via AppIndicator |
| Secrets | GNOME Keyring (secretstorage) on the laptop, Android Keystore on the tablet |
| Android | Kotlin, Jetpack Compose, minSdk 30 (Android 11, needed for wireless debugging), targetSdk 36 (Android 16) |
| Control protocol | JSON messages over TLS WebSocket; keys pinned at pairing |
| Tests | pytest with fake adb / nmcli / tailscale; JUnit on Android; manual hardware checklist |

### Core interfaces (laptop)

```python
@dataclass
class Path:
    mode: Literal["usb", "lan", "laptop_hotspot", "tablet_hotspot", "tailscale", "bluetooth"]
    tablet_addr: str        # where the laptop reaches the tablet
    laptop_addr: str        # where the tablet reaches the laptop ("127.0.0.1" on USB)
    adb_serial: str | None  # None on Bluetooth
    rtt_ms: float
    video_ok: bool          # False on Bluetooth

class PathProvider(Protocol):
    mode: str
    priority: int           # lower = preferred
    async def probe(self) -> Path | None: ...
    async def activate(self, path: Path) -> None: ...   # adb reverse, adb tcpip, hotspot on
    async def deactivate(self) -> None: ...

class Service(Protocol):
    name: str
    needs_video: bool
    async def on_path_changed(self, path: Path | None) -> None: ...
```

### Control messages (port 47800)

| Type | Direction | Carries |
|---|---|---|
| hello | both | device id, app version, key fingerprint |
| path | laptop to tablet | active mode, laptop address to use |
| status | laptop to tablet | connected / syncing / offline, per service |
| clip | both | clipboard text |
| remote_ready | tablet to laptop | "make sure RustDesk is up"; laptop replies with address and port |

### Config file

`~/.config/tablink/config.toml` (passwords live in the keyring, never here):

```toml
[device]
tablet_name = "Redmi Pad 2"
tablet_serial = ""              # filled in at pairing

[network]
control_port = 47800
known_tablet_addrs = []         # learned automatically
hotspot_ssid = "TabLink"

[video]
high     = { max_size = 2560, bitrate = "16M", fps = 60 }
balanced = { max_size = 1600, bitrate = "8M",  fps = 60 }
low      = { max_size = 1024, bitrate = "2M",  fps = 30 }

[tailscale]
enabled = false
```

### CLI

| Command | Does |
|---|---|
| `tablink` (icon click) | Connect on the best path and open the tablet screen |
| `tablink pair` | First-time wizard over USB |
| `tablink status` | Active mode, delay, state of each service |
| `tablink hotspot on` / `off` | Start or stop the laptop hotspot (mode 3) |
| `tablink send FILE...` | Send files to the tablet |
| `tablink doctor` | Test every mode and print a fix for each failure |
| `tablink path --force MODE` | Pin one mode, for testing |

### Task list (in order)

- [ ] T1 Scaffold repo and CI (ruff, mypy, pytest, Gradle build). Check: CI green.
- [ ] T2 config.py, CLI skeleton, `tablink status`. Check: config load/save tests pass.
- [ ] T3 adb.py (devices, connect, tcpip, forward, reverse, shell) with a fake adb. Check: tests pass
  with no device attached.
- [ ] T4 USB path + scrcpy service + .desktop icon. Check: with the cable in, one click shows the
  tablet (F1).
- [ ] T5 Path manager: priorities, 5-second health check, switching. Check: simulated path loss
  switches to the next path.
- [ ] T6 LAN path: mDNS, saved addresses, wireless debugging pairing. Check: F1 over router Wi-Fi.
- [ ] T7 Laptop hotspot path via nmcli. Check: `tablink hotspot on`, tablet joins, F1 works.
- [ ] T8 Tablet hotspot path: adb tcpip bootstrap over USB, gateway detection. Check: F1 with the
  laptop on the tablet's hotspot.
- [ ] T9 Control channel server (TLS WebSocket, pinned keys). Check: paired client connects; unknown
  key refused.
- [ ] T10 Tailscale path (`tailscale status --json`). Check: F1 on the Low preset with the devices on
  two different networks.
- [ ] T11 Bluetooth PAN path. Check: clipboard works with Wi-Fi off; screen control shows a clear
  "too slow" message.
- [ ] T12 Android app: MainActivity, LinkService, ControlClient, Discovery. Check: app shows
  "Connected via \<mode\>" in every mode.
- [ ] T13 RustDesk: laptop keeps RustDesk up with direct IP on; app opens the session. Check: F2 on
  USB and LAN.
- [ ] T14 Clipboard: scrcpy sync during F1, clip messages, Quick Settings tile. Check: both F3 tests.
- [ ] T15 Files and folder: KDE Connect share + Syncthing REST API auto-setup of the TabLink folder.
  Check: F4 and F5 tests.
- [ ] T16 SSH + code-server offload. Check: F6 test.
- [ ] T17 GTK pairing wizard. Check: fresh laptop and reset tablet to working in one run.
- [ ] T18 `tablink doctor`. Check: unplugging, Wi-Fi off and hotspot off each produce the right fix
  message.
- [ ] T19 install.sh, systemd user unit, autostart. Check: F10 test after reboot.

### Rules for Claude Code (repeated in CLAUDE.md)

- Never require root on the tablet or a bootloader unlock.
- Each external tool (adb, scrcpy, nmcli, tailscale, syncthing, rustdesk) is called from exactly one
  wrapper module, and each wrapper has a fake for tests.
- Never hardcode IP addresses or hotspot subnets; always discover them.
- No secrets in files or logs.
- Ask before adding a dependency not listed in Stack.

## Testing and acceptance criteria

v1 is done when every check below passes on the real laptop and Redmi Pad 2, on home Wi-Fi and over
USB.

- [ ] F1: one click on the laptop icon shows the tablet screen in 5 seconds or less; typing, clicks
  and scrolling work
- [ ] F1: input-to-screen delay feels under 100 ms on Wi-Fi (record a stopwatch on video to check)
- [ ] F2: one tap on the tablet shows the laptop desktop; touch, right-click, scroll and keyboard
  work
- [ ] F3: text copied on the laptop pastes on the tablet within 2 seconds
- [ ] F3: text copied on the tablet pastes on the laptop during a session, and with one tile tap
  otherwise
- [ ] F4: a 100 MB file dragged onto the tablet window arrives intact (same checksum)
- [ ] F4: a photo shared from the tablet gallery arrives on the laptop
- [ ] F5: a file saved in TabLink on either side appears on the other within 10 seconds
- [ ] F5: editing the same file on both sides offline produces a conflict copy, never lost data
- [ ] F6: from the tablet, `python3 train.py` runs on the laptop and its output streams live
- [ ] F7: a message notification on the tablet appears on the laptop
- [ ] F10: after a laptop reboot and tablet restart, both one-click icons still work without
  re-pairing (USB re-plug allowed only if wireless debugging was reset)
- [ ] Security: a third device on the same Wi-Fi cannot connect to any TabLink port
- [ ] Battery: 1 hour of F1 on Wi-Fi uses less than 25% of the tablet battery

### Connection modes

- [ ] Mode 1: with Wi-Fi off on both devices, the USB cable alone gives F1, F2, F3 and F5
- [ ] Mode 2: F1 and F2 work on the home router
- [ ] Mode 3: with no router nearby, `tablink hotspot on` starts the hotspot, the tablet joins, and F1
  and F2 work
- [ ] Mode 4: with the laptop on the tablet's hotspot, F1 works after one USB bootstrap
- [ ] Mode 5: with both devices on a phone's hotspot, F1 and F2 work
- [ ] Mode 6: on campus Wi-Fi, TabLink falls back on its own and names the mode it chose
- [ ] Mode 7: laptop at home, tablet on another network elsewhere; F2 and F5 work on the Low preset
- [ ] Mode 8: Wi-Fi off, Bluetooth on; clipboard works and screen control shows a clear message
- [ ] Switching: pulling the USB cable mid-session moves to Wi-Fi and the screen returns within 3
  seconds

## Sources

- scrcpy (Wikipedia): v4.0 released May 2026
- scrcpy 4.1 release notes (Forest Watch): July 2026, media scan after file transfer
- RustDesk for Linux guide: Wayland support and per-session consent
- RustDesk unattended Wayland preview (AlternativeTo): August 2026, Debian/Ubuntu x86_64
- GSConnect features wiki
- KDE forum: one-way clipboard sync on Android
- Redmi Pad 2 port notes (GitHub): Helio G100-Ultra (MT6789) hardware

## Decisions since the original spec (2026-10-24)

Agreed with the owner during the build; where these differ from the sections above, these win.

| Topic | Original spec | Now | Why |
|---|---|---|---|
| Tablet | Redmi Pad 2, codename taiko | Redmi Pad 2 4G, codename **koto** (same MT6789) | What the real tablet reports |
| Tablet controls laptop (F2) | RustDesk | **GNOME Remote Desktop (RDP)** on the laptop, **FreeRDP built into the TabLink app** (from source, `android/scripts/build-freerdp.sh`) | No per-session Wayland prompt; one app on the tablet |
| Clipboard, files, notifications (F3, F4, F7) | KDE Connect / GSConnect | **Built into TabLink**: control channel + a small TabLink GNOME Shell extension for the laptop clipboard | One app; GNOME only lets the shell read the clipboard in the background |
| Third-party tablet apps | RustDesk, KDE Connect, Syncthing-Fork | **None** (Syncthing for F5 still to decide) | Owner's request: everything in one app |
| Wi-Fi adb | Wireless debugging (LAN), `adb tcpip` (tablet hotspot) | **`adb tcpip 5555` on every IP link**, enabled over USB once per tablet boot | Wireless debugging turns itself off on every network change |
| Service tunnel | `adb reverse` on USB only | **`adb reverse` on every adb link** (USB and Wi-Fi), so the tablet always dials 127.0.0.1 | One code path; control port never exposed to the network |
| Control channel | TLS WebSocket, keys pinned at pairing | **JSON lines on 127.0.0.1:47800** through the tunnel; each connection proves pairing with **HMAC-SHA256 challenge-response**; token in GNOME keyring and Android Keystore | Loopback-only port; secret never crosses the link |
| Ports | RustDesk 21118, KDE Connect 1716 | **RDP 3389**; SSH reaches the tablet as 127.0.0.1:2222 (Android cannot bind ports below 1024) | Follows the engine change |
| Pairing | GTK wizard | **`tablink pair`** (CLI, every step idempotent); GTK wizard later | Working path first |
| scrcpy | 4.x | **5.0.1** (no breaking changes, hardware decoding) | Released after the spec |
| Internet mode (7) | Tailscale app/daemon on both devices | **Tailscale built into TabLink on both sides** (tsnet, no VPN, no separate install): `android/tsnet` in the app, `laptop/tailnet` helper run by the daemon; keys in Android Keystore / GNOME keyring; diagnostic log upload off | Owner's request: nothing separate to install |
| Unlocking from the laptop | Not specified | **Unlock pad** in the laptop bar (pattern or PIN), opens by itself while the tablet is locked | Android blanks the lock screen's pattern/PIN view in every mirror |
| Waking | Not specified | **Wake tablet** (laptop menu) and **Wake laptop** (tablet app: screen on, or Wake-on-Wi-Fi magic packet on the same network) | A sleeping laptop cannot be reached from the internet |
