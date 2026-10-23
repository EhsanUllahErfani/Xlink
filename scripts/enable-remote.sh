#!/usr/bin/env bash
# Far-away access for TabLink (run once, as yourself; asks for your password):
#  1. installs Tailscale (official installer) and signs this laptop in,
#  2. lets your user read Tailscale's status (TabLink uses it to find the tablet),
#  3. turns on Wake-on-Wi-Fi (magic packet) for the Wi-Fi network you are on now.
set -euo pipefail

if ! command -v tailscale >/dev/null; then
  echo "== Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "== Signing in to Tailscale: open the link it prints and sign in"
sudo tailscale up --operator="$USER"

wifi="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2=="802-11-wireless"{print $1; exit}')"
if [ -n "$wifi" ]; then
  echo "== Wake-on-Wi-Fi for \"$wifi\""
  nmcli connection modify "$wifi" 802-11-wireless.wake-on-wlan magic
  echo "   on (takes effect the next time this Wi-Fi connects)"
else
  echo "== Not on Wi-Fi now: Wake-on-Wi-Fi skipped (run this again while on Wi-Fi)"
fi

echo "== Done. Tailscale address of this laptop: $(tailscale ip -4 2>/dev/null || echo '?')"
echo "TABLINK_REMOTE_DONE"
