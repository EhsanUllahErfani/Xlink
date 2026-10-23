#!/usr/bin/env bash
# Build FreeRDP's Android library (freeRDPCore, an .aar with the native RDP engine) from
# source, pinned to one release, and put it where the TabLink app expects it.
#
#   android/scripts/build-freerdp.sh            # arm64-v8a (Redmi Pad 2 and most tablets)
#   ABIS="arm64-v8a;armeabi-v7a" android/scripts/build-freerdp.sh
#
# Needs: JDK 17+, Android SDK with NDK 29.0.13113456 and CMake 4.1.2 (ANDROID_HOME set),
# internet on the first run (FreeRDP's superbuild fetches OpenSSL, FFmpeg, ...).
set -euo pipefail

FREERDP_TAG=3.32.2
FREERDP_COMMIT=97eff6ddfbbfaa8e42a126d362686bd001ff8bdc
ABIS="${ABIS:-arm64-v8a}"

here="$(cd "$(dirname "$0")" && pwd)"
android="$(dirname "$here")"
src="$android/third_party/FreeRDP"
out="$android/app/libs/freeRDPCore-release.aar"
: "${ANDROID_HOME:=$HOME/Android/Sdk}"
export ANDROID_HOME

if [ ! -d "$src/.git" ]; then
  git clone --quiet --depth 1 --branch "$FREERDP_TAG" https://github.com/FreeRDP/FreeRDP.git "$src"
fi
actual="$(git -C "$src" rev-parse HEAD)"
if [ "$actual" != "$FREERDP_COMMIT" ]; then
  echo "FreeRDP checkout is $actual, expected $FREERDP_COMMIT ($FREERDP_TAG); refusing to build." >&2
  exit 1
fi

# TabLink's small changes to FreeRDP (each patch says why).
for patch in "$here"/patches/*.patch; do
  if git -C "$src" apply --reverse --check "$patch" 2>/dev/null; then
    continue  # already applied
  fi
  git -C "$src" apply "$patch"
done

studio="$src/client/Android/Studio"
echo "sdk.dir=$ANDROID_HOME" > "$studio/local.properties"
cat > "$studio/release.properties" <<PROPS
ABI_FILTERS=$ABIS
SPLIT_ARCHITECTURES=$ABIS
SPLIT_ENABLED=false
BUILD_UNIVERSAL=false
PROPS

(cd "$studio" && ./gradlew --no-daemon --console=plain -q :freeRDPCore:assembleRelease)

mkdir -p "$(dirname "$out")"
cp "$studio/freeRDPCore/build/outputs/aar/freeRDPCore-release.aar" "$out"
echo "built $out ($(du -h "$out" | cut -f1)) from FreeRDP $FREERDP_TAG for $ABIS"
