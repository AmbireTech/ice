#!/usr/bin/env bash
# Builds for the host: build/fqrdecode, the device's QR decoder (the firmware's
# source, untouched), and build/qrdecode, the quirc-based decoder it replaced
# (quirc/, kept as the reference to compare against).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cam="$here/../../../trezor-firmware/core/embed/io/camera"
mkdir -p "$here/build"
q="$here/quirc"
cc -O2 -std=c11 -w -I "$here/stub" -I "$q" -I "$here/../qrcommon" \
  -o "$here/build/qrdecode" "$here/qrdecode.c" "$here/../qrcommon/qr_roi.c" "$q/qr_scan.c" "$q/quirc_decode.c" "$q/quirc_identify.c" \
  "$q/quirc_version_db.c" -lm
# build/fqrdecode: the video decoder (fqr.c) alone, with its tuning switches
cc -O2 -std=c11 -Wall -Wextra -DFQR_HOST_TUNE -DFQR_PROF -I "$cam/qr" \
  -o "$here/build/fqrdecode" "$here/fqrdecode.c" "$cam/qr/fqr.c" -lm
# build/scansim: the scan loop (the firmware's wrapper, exposure control and QR
# encoder) on a simulated screen and sensor
cc -O2 -std=c11 -Wall -Wextra -DCAMERA_AE_HOST -DHOST_SYSTICK_FN=scansim_ms -I "$here/stub" -I "$cam/inc" -I "$cam/qr" \
  -I "$cam/stm32u5" -I "$here/../../../trezor-firmware/vendor/QR-Code-generator/c" \
  -o "$here/build/scansim" "$here/scansim.c" "$cam/qr/qr_scan.c" "$cam/qr/fqr.c" \
  "$cam/stm32u5/camera_ae.c" "$here/../../../trezor-firmware/vendor/QR-Code-generator/c/qrcodegen.c" -lm
# build/fqrtest: the decoder's Reed-Solomon and payload parser, under ASan/UBSan
cc -O1 -g -std=c11 -fsanitize=address,undefined -fno-sanitize-recover=undefined \
  -o "$here/build/fqrtest" "$here/fqrtest.c" -lm
"$here/build/fqrtest"
echo "$here/build/qrdecode"
echo "$here/build/fqrdecode"
echo "$here/build/scansim"
