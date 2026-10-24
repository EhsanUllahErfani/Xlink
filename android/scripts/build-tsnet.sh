#!/usr/bin/env bash
# Build TabLink's built-in Tailscale node (android/tsnet, Tailscale's tsnet library) into
# app/libs/tablinknet.aar with gomobile. Versions are pinned in android/tsnet/go.mod + go.sum.
#
# Needs: Go 1.27+, gomobile/gobind (go install golang.org/x/mobile/cmd/{gomobile,gobind}@<the
# version in go.mod>), Android SDK with NDK 29.0.13113456.
set -euo pipefail

ABI_TARGETS="${ABI_TARGETS:-android/arm64}"
here="$(cd "$(dirname "$0")" && pwd)"
android="$(dirname "$here")"
: "${ANDROID_HOME:=$HOME/Android/Sdk}"
export ANDROID_HOME
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-$ANDROID_HOME/ndk/29.0.13113456}"
: "${JAVA_HOME:=$HOME/.local/opt/jdk-17}"  # CI sets its own
export JAVA_HOME
export PATH="$HOME/.local/opt/go/bin:$HOME/go/bin:$JAVA_HOME/bin:$PATH"

mkdir -p "$android/app/libs"
cd "$android/tsnet"
go mod verify >/dev/null
gomobile bind -target="$ABI_TARGETS" -androidapi 30 -javapkg dev.tablink \
  -ldflags="-s -w" -trimpath -o "$android/app/libs/tablinknet.aar" .
rm -f "$android/app/libs/tablinknet-sources.jar"
echo "built $android/app/libs/tablinknet.aar ($(du -h "$android/app/libs/tablinknet.aar" | cut -f1)) for $ABI_TARGETS"
