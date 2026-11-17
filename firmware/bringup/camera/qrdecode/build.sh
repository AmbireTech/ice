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
# build/fqrtest: the decoder's Reed-Solomon and payload parser, under ASan/UBSan
cc -O1 -g -std=c11 -fsanitize=address,undefined -fno-sanitize-recover=undefined \
  -o "$here/build/fqrtest" "$here/fqrtest.c" -lm
"$here/build/fqrtest"
echo "$here/build/qrdecode"
echo "$here/build/fqrdecode"
