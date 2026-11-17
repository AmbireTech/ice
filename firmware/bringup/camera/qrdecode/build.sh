#!/usr/bin/env bash
# Builds build/qrdecode: the device's QR decoder (firmware sources, untouched)
# for the host.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cam="$here/../../../trezor-firmware/core/embed/io/camera"
mkdir -p "$here/build"
cc -O2 -std=c11 -w -I "$here/stub" -I "$cam/inc" -I "$cam/qr" -I "$here/../qrcommon" \
  -o "$here/build/qrdecode" "$here/qrdecode.c" "$here/../qrcommon/qr_roi.c" "$cam/qr/qr_scan.c" "$cam/qr/quirc_decode.c" "$cam/qr/quirc_identify.c" \
  "$cam/qr/quirc_version_db.c" -lm
echo "$here/build/qrdecode"
