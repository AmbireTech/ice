#!/usr/bin/env bash
# Build Trezor's Bluetooth app (nordic/trezor/trezor-ble) for our radio, the
# Ebyte E73-2G4M08S1C (nRF52840) on board ambire_p0, in Docker.
#
#   firmware/radio/build.sh              # build -> firmware/radio/out/ambire_p0/
#   DEBUG=1 firmware/radio/build.sh      # + Trezor's debug.conf (RTT logging,
#                                        #   asserts) -> out/ambire_p0-debug/
#   firmware/radio/build.sh --shell      # a shell in the build workspace
#
# The nRF Connect SDK version is the one Trezor's manifest pins
# (nordic/trezor/west.yml). The SDK checkout, the Zephyr SDK toolchain and the
# Python packages live in the Docker volume $VOLUME, so only the first run
# downloads them (several GB); a changed manifest re-runs `west update`.
# Runs natively on arm64 and x86_64 hosts (Zephyr's multi-arch ci-base image).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# The whole repo is mounted (read-only): the app reaches trezor-firmware/crypto
# through a symlink, and the build stamps this repo's git commit. Physical
# paths: the checkout may be reached through a symlink.
repo="$(cd "$(git -C "$here" rev-parse --show-toplevel)" && pwd -P)"
manifest_dir="$(cd "$here/../trezor-firmware/nordic/trezor" && pwd -P)"
manifest_rel="${manifest_dir#"$repo"/}"
BOARD="${BOARD:-ambire_p0}"
DOCKER="${DOCKER:-docker}"
VOLUME="${VOLUME:-ambire-radio-ncs}"
# Zephyr's CI base image (Ubuntu 24.04, cmake, ninja, dtc, west), pinned.
IMAGE="${IMAGE:-ghcr.io/zephyrproject-rtos/ci-base@sha256:f09a9642ff7558a21f5d0a2a720844c228a73b0d3d3e6112f1cfd7f6d8970bae}"
out="$here/out/$BOARD"
extra_conf=""
if [ "${DEBUG:-0}" = 1 ]; then
  extra_conf="-DOVERLAY_CONFIG=debug.conf"
  out="$here/out/$BOARD-debug"
fi
mkdir -p "$out"

mode=build
if [ "${1:-}" = "--shell" ]; then
  mode=shell
fi

tty_flags=()
if [ -t 0 ] && [ -t 1 ]; then
  tty_flags=(-it)
fi

# shellcheck disable=SC2016  # expanded inside the container
"$DOCKER" run --rm ${tty_flags[@]+"${tty_flags[@]}"} \
  -v "$VOLUME:/ncs" \
  -v "$repo:/repo:ro" \
  -v "$out:/out" \
  -e BOARD="$BOARD" -e EXTRA_CONF="$extra_conf" -e MODE="$mode" \
  -e MANIFEST="/repo/$manifest_rel" \
  -w /ncs \
  "$IMAGE" bash -c '
set -euo pipefail
cd /ncs

git config --global --add safe.directory "*"

# 1. NCS workspace from Trezor manifest (west update only when it changed).
# The manifest repo sits in the workspace as a link into the mounted tree.
if [ ! -L trezor ]; then
  if [ -d trezor ]; then rmdir trezor; fi
  ln -s "$MANIFEST" trezor
fi
if [ ! -d .west ]; then
  west init -l --mf west.yml trezor
fi
want="$(sha256sum trezor/west.yml | cut -c1-16)"
if [ "$(cat .manifest-stamp 2>/dev/null || true)" != "$want" ]; then
  echo ">>> west update (manifest $want)"
  west update --narrow -o=--depth=1
  echo "$want" > .manifest-stamp
fi

# 2. Zephyr SDK: the version this Zephyr asks for, arm toolchain only.
sdk_ver="$(cat zephyr/SDK_VERSION)"
sdk="/ncs/toolchains/zephyr-sdk-$sdk_ver"
case "$(uname -m)" in
  aarch64) host=linux-aarch64 ;;
  x86_64) host=linux-x86_64 ;;
  *) echo "unsupported host $(uname -m)" >&2; exit 1 ;;
esac
if [ ! -f "$sdk/.installed" ]; then
  echo ">>> Zephyr SDK $sdk_ver ($host)"
  mkdir -p /ncs/toolchains
  base="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v$sdk_ver"
  rm -rf "$sdk"
  wget -q -O /tmp/sdk.tar.xz "$base/zephyr-sdk-${sdk_ver}_${host}_minimal.tar.xz"
  tar -xf /tmp/sdk.tar.xz -C /ncs/toolchains
  rm /tmp/sdk.tar.xz
  (cd "$sdk" && ./setup.sh -t arm-zephyr-eabi -c)
  touch "$sdk/.installed"
fi
export ZEPHYR_SDK_INSTALL_DIR="$sdk"
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr

# 3. Python packages NCS needs on top of the image (keyed by requirements).
req_hash="$(cat zephyr/scripts/requirements-base.txt nrf/scripts/requirements-build.txt | sha256sum | cut -c1-16)"
venv=/ncs/venv
if [ "$(cat "$venv/.stamp" 2>/dev/null || true)" != "$req_hash" ]; then
  echo ">>> python requirements"
  rm -rf "$venv"
  python3 -m venv --system-site-packages "$venv"
  "$venv/bin/pip" install -q -r zephyr/scripts/requirements-base.txt -r nrf/scripts/requirements-build.txt
  echo "$req_hash" > "$venv/.stamp"
fi
export PATH="$venv/bin:$PATH"

if [ "$MODE" = shell ]; then
  exec bash
fi

# 4. The app, without sysbuild: no MCUboot, a plain image linked at 0x0.
echo ">>> west build -b $BOARD $EXTRA_CONF"
west build --pristine=always --no-sysbuild -b "$BOARD" -d "/ncs/build/$BOARD" trezor/trezor-ble -- $EXTRA_CONF
cp "/ncs/build/$BOARD/zephyr/zephyr.hex" "/ncs/build/$BOARD/zephyr/zephyr.elf" "/ncs/build/$BOARD/zephyr/.config" /out/
mv /out/.config /out/zephyr.config
'

echo ">>> artifacts in $out"
ls -l "$out"
