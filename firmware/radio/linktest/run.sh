#!/usr/bin/env bash
# Host test of the radio's framed UART link (no hardware, no SDK): compiles
# uart_link.c against small stand-ins for the Zephyr APIs it uses.
#   firmware/radio/linktest/run.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
fw="$here/../../trezor-firmware"
link="$fw/nordic/trezor/trezor-ble/src/trz_comm"
build="$here/../out/linktest"
mkdir -p "$build/zephyr/pm" "$build/zephyr/sys" "$build/zephyr/drivers" "$build/zephyr/logging"
for h in device devicetree kernel types pm/device_runtime sys/atomic sys/crc sys/ring_buffer drivers/uart logging/log; do
  echo '#include "zstub.h"' > "$build/zephyr/$h.h"
done
printf '#pragma once\n#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n' > "$build/trezor_types.h"

"${CC:-cc}" -std=c11 -Wall -Wextra -Wno-unused-parameter \
  -I"$here/stub" -I"$build" -I"$link" -I"$link/inc" \
  "$here/test_uart_link.c" "$fw/core/embed/io/nrf/crc8.c" -I"$fw/core/embed/io/nrf" \
  -o "$build/test_uart_link"
"$build/test_uart_link"
