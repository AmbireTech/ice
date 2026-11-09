#!/usr/bin/env bash
# Host tests of the airgapped QR flow (firmware/README.md, "Airgapped
# signing"): test vectors from the Ambire app's own libraries, the firmware's
# UR codec in Rust, its Python package under CPython (with Trezor's Ethereum
# code computing the digests), and the device's own URs decoded by the app's
# libraries. Needs node, cargo, python3 with pycryptodome, and the Ambire app
# checkout with its node_modules (AMBIRE_APP, default ~/Projects/ambire-app).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
out="${1:-$(mktemp -d)}"
mkdir -p "$out"

node "$here/vectors.js" "$out/vectors.json"
(cd "$here/rust" && VECTORS="$out/vectors.json" cargo test --offline --quiet)
python3 "$here/test_airgap.py" "$out/vectors.json" "$out"
enc="$here/rust/target/debug/ur_encode"
(cd "$here/rust" && cargo build --offline --quiet --bin ur_encode)
"$enc" crypto-hdkey "$(cat "$out/hdkey.hex")" > "$out/hdkey.ur"
"$enc" eth-signature "$(cat "$out/signature.hex")" > "$out/signature.ur"
node "$here/decode.js" "$out/vectors.json" "$out"
echo "all airgap host tests passed ($out)"
