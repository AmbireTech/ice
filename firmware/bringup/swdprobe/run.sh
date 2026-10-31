#!/usr/bin/env bash
# Copy swdprobe.c to the probe board, build it there and run it.
#   firmware/bringup/swdprobe/run.sh [user@host] [reads]   (default debian@192.168.7.2, 20)
# No OpenOCD may run on the probe board meanwhile (both want the same pins).
set -euo pipefail
host="${1:-debian@192.168.7.2}"
reads="${2:-20}"
here="$(cd "$(dirname "$0")" && pwd)"
scp -q "$here/swdprobe.c" "$host:/tmp/swdprobe.c"
ssh "$host" "pkill -f '^openocd' || true; cc -O2 -Wall -o /tmp/swdprobe /tmp/swdprobe.c -lgpiod && /tmp/swdprobe GPIO25 GPIO24 $reads"
