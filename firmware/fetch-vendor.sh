#!/usr/bin/env bash
# Fetch trezor-firmware's submodules at the commits pinned in vendor-pins.txt.
# The firmware tree is vendored into this repo without its .git, so
# `git submodule update` cannot run; each submodule becomes its own shallow
# checkout instead (ignored by this repo, see .gitignore).
#
#   firmware/fetch-vendor.sh            # all pinned submodules
#   firmware/fetch-vendor.sh vendor/nanopb vendor/micropython
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tree="$here/trezor-firmware"

grep -v '^#' "$here/vendor-pins.txt" | while read -r sub url commit; do
  if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qx "$sub"; then
    continue
  fi
  dir="$tree/$sub"
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" = "$commit" ]; then
    echo "ok       $sub @ ${commit:0:12}"
    continue
  fi
  echo "fetch    $sub @ ${commit:0:12}"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$url"
  git -C "$dir" fetch -q --depth 1 origin "$commit"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  git -C "$dir" submodule update -q --init --recursive --depth 1
done
