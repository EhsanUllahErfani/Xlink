#!/usr/bin/env bash
# Install TabLink on this laptop (Ubuntu, GNOME). Safe to run again: it updates in place.
#
#   scripts/install.sh
#
# Then plug in the tablet and run:  tablink pair
#
# What it does (no sudo unless system packages are missing; it asks first):
#   - checks the Ubuntu packages TabLink needs and offers the one apt command to add them
#   - creates TabLink's own Python environment from Ubuntu's Python (not conda/pyenv)
#   - installs scrcpy 5.0.1 (checksum-verified) if missing
#   - builds the built-in Tailscale helper if Go is available (internet access; optional)
#   - adds the `tablink` command, the TabLink app icon, and the background service
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
share="$HOME/.local/share/tablink"
venv="$share/venv"
bin="$HOME/.local/bin"
SCRCPY_VERSION=v5.0.1
SCRCPY_SHA256=9f969d30cc574816077edecda65719c1c70b0aee1df1ba5fa00779ac07b8fd6e

say() { printf '\033[1m==\033[0m %s\n' "$*"; }

# 1. System packages -----------------------------------------------------------------------
need=()
for pkg in python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gnome-remote-desktop \
           android-sdk-platform-tools-common; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg")
done
command -v adb >/dev/null || [ -x "$HOME/Android/Sdk/platform-tools/adb" ] || need+=(adb)
if [ ${#need[@]} -gt 0 ]; then
  say "Missing Ubuntu packages: ${need[*]}"
  read -r -p "Install them now with sudo apt? [Y/n] " ans
  if [[ "${ans:-y}" =~ ^[Yy] ]]; then
    sudo apt-get update && sudo apt-get install -y "${need[@]}"
  else
    echo "Install them, then run this again."; exit 1
  fi
fi

# 2. Python environment (Ubuntu's own Python, with its GTK bindings) -------------------------
say "Python environment in $venv"
mkdir -p "$share" "$bin"
if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c "import gi" 2>/dev/null; then
  rm -rf "$venv"
  /usr/bin/python3 -m venv --system-site-packages "$venv"
fi
"$venv/bin/pip" install --quiet --upgrade pip
"$venv/bin/pip" install --quiet -e "$repo/laptop"
ln -sf "$venv/bin/tablink" "$bin/tablink"

# 3. scrcpy (tablet window) ---------------------------------------------------------------
if ! command -v scrcpy >/dev/null && [ ! -x "$bin/scrcpy" ]; then
  say "Installing scrcpy $SCRCPY_VERSION"
  tmp="$(mktemp -d)"
  f="scrcpy-linux-x86_64-$SCRCPY_VERSION.tar.gz"
  curl -fsSL -o "$tmp/$f" "https://github.com/Genymobile/scrcpy/releases/download/$SCRCPY_VERSION/$f"
  echo "$SCRCPY_SHA256  $tmp/$f" | sha256sum -c - >/dev/null
  rm -rf "$HOME/.local/opt/scrcpy" && mkdir -p "$HOME/.local/opt/scrcpy"
  tar -xzf "$tmp/$f" -C "$HOME/.local/opt/scrcpy" --strip-components=1
  ln -sf "$HOME/.local/opt/scrcpy/scrcpy" "$bin/scrcpy"
  rm -rf "$tmp"
fi

# 4. Built-in Tailscale helper (optional: internet access) ---------------------------------
go="$(command -v go || true)"; [ -z "$go" ] && [ -x "$HOME/.local/opt/go/bin/go" ] && go="$HOME/.local/opt/go/bin/go"
if [ -n "$go" ]; then
  say "Building the built-in Tailscale helper"
  (cd "$repo/laptop/tailnet" && CGO_ENABLED=0 "$go" build -trimpath -ldflags="-s -w" -o tablink-tailnet .)
  ln -sf "$repo/laptop/tailnet/tablink-tailnet" "$bin/tablink-tailnet"
else
  say "Go not found: internet access (Tailscale) skipped; everything else works"
fi

# 5. App icon, menu entry, background service ----------------------------------------------
say "App icon and background service"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps" \
         "$HOME/.config/systemd/user"
cp "$repo/laptop/data/icons/hicolor/scalable/apps/tablink.svg" \
   "$HOME/.local/share/icons/hicolor/scalable/apps/"
sed "s|^Exec=tablink |Exec=$bin/tablink |" "$repo/laptop/data/tablink.desktop" \
   > "$HOME/.local/share/applications/tablink.desktop"
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cp "$repo/laptop/data/tablinkd.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable tablinkd.service >/dev/null 2>&1
systemctl --user restart tablinkd.service

say "Done. Next: plug in the tablet (USB debugging on) and run:  tablink pair"
