#!/usr/bin/env bash
# Builds the host decoder harness from the firmware's vendored quirc:
# build/harness as the firmware decodes, and build/harness-<variant> with the
# fixes of ext_identify.c (the firmware tree is not touched).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
qr="$here/../../../trezor-firmware/core/embed/io/camera/qr"
mkdir -p "$here/build"
cc -O2 -std=c11 -w -I "$qr" -o "$here/build/harness" "$here/harness.c" \
  "$qr/quirc_decode.c" "$qr/quirc_identify.c" "$qr/quirc_version_db.c" -lm
ext() {  # ext NAME FLAGS...
  local name="$1"; shift
  cc -O2 -std=c11 -w -I "$qr" -I "$here" "$@" -o "$here/build/harness-$name" "$here/harness.c" \
    "$qr/quirc_decode.c" "$here/ext_identify.c" "$qr/quirc_version_db.c" -lm
}
ext aff -DEXT_AFFINE=1
ext regrid -DEXT_REGRID=1
ext ext -DEXT_AFFINE=1 -DEXT_REGRID=1
ls "$here/build"/harness*
