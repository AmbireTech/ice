#!/bin/sh
# Export the tscircuit netlist to board/netlist.circuit.json.
#
#     scripts/exportNetlist.sh
#
# `tsci export` reads every file under the entry file's directory, and the
# vendored Trezor tree (firmware/trezor-firmware) carries symlinks that point
# outside it, which stop the export with ENOENT. So the export runs in a staging
# directory holding only the netlist's own sources.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd -P)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -R "$HERE/index.circuit.tsx" "$HERE/src" "$HERE/package.json" "$HERE/tsconfig.json" \
  "$HERE/tscircuit.config.json" "$HERE/tscircuit.config.ts" "$STAGE/"
ln -s "$HERE/node_modules" "$STAGE/node_modules"
(cd "$STAGE" && npx tsci export index.circuit.tsx -f circuit-json -o netlist.circuit.json)
cp "$STAGE/netlist.circuit.json" "$HERE/board/netlist.circuit.json"
echo "wrote board/netlist.circuit.json"
