#!/usr/bin/env python3
"""Statistical sanity check of the device's strong random number generator.

    entropy_test.py collect --calls 16 --out sample.bin   # needs the device: 16 confirmations
    entropy_test.py analyze sample.bin

`collect` asks the device for GetEntropy (1024 bytes per call, the firmware's
maximum; each call is confirmed on the device). The firmware serves it from
rng_fill_buffer_strong: the MCU's hardware generator XORed with the SE050's.

`analyze` runs quick tests that catch a broken or stuck generator (they do not
certify a good one): bit balance, runs, byte-frequency chi-square, serial
correlation, compressibility, repeated 16-byte blocks across the sample.
Thresholds are wide (about 4 sigma) so a healthy generator passes almost always.
"""
import argparse
import math
import subprocess
import sys
import zlib
from pathlib import Path


def collect(trezorctl: str, calls: int, out: Path) -> None:
    data = bytearray()
    for i in range(calls):
        print(f"call {i + 1}/{calls}: confirm on the device", flush=True)
        p = subprocess.run([trezorctl, "-p", "webusb", "crypto", "get-entropy", "1024"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if p.returncode != 0:
            # urllib3's LibreSSL warning comes first on macOS: show the lines that matter
            lines = [l for l in (p.stdout + p.stderr).splitlines() if "arning" not in l]
            sys.exit(f"get-entropy failed (exit {p.returncode}): {' | '.join(lines)}"
                     " (GetEntropy needs an initialized device)")
        hexline = [l.strip() for l in p.stdout.splitlines() if l.strip() and all(c in "0123456789abcdef" for c in l.strip())]
        if not hexline:
            sys.exit(f"no hex in the output: {p.stdout.strip()}")
        chunk = bytes.fromhex(hexline[-1])
        if len(chunk) != 1024:
            sys.exit(f"expected 1024 bytes, got {len(chunk)}")
        data += chunk
    out.write_bytes(bytes(data))
    print(f"wrote {len(data)} bytes to {out}")


def analyze(path: Path) -> bool:
    d = path.read_bytes()
    n = len(d)
    nbits = n * 8
    ok = True

    def report(name: str, value: str, passed: bool) -> None:
        nonlocal ok
        ok &= passed
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {value}")

    print(f"{path}: {n} bytes")

    ones = sum(bin(b).count("1") for b in d)
    z = (ones - nbits / 2) / math.sqrt(nbits / 4)
    report("bit balance", f"{ones / nbits:.5f} ones (z = {z:+.2f})", abs(z) < 4)

    bits = "".join(f"{b:08b}" for b in d)
    runs = 1 + sum(1 for i in range(1, len(bits)) if bits[i] != bits[i - 1])
    p = ones / nbits
    exp = 2 * nbits * p * (1 - p)
    sd = 2 * math.sqrt(nbits) * p * (1 - p)
    zr = (runs - exp) / sd
    report("runs", f"{runs} (expected {exp:.0f}, z = {zr:+.2f})", abs(zr) < 4)

    counts = [0] * 256
    for b in d:
        counts[b] += 1
    e = n / 256
    chi = sum((c - e) ** 2 / e for c in counts)
    zc = (chi - 255) / math.sqrt(2 * 255)  # chi-square(255) ~ normal for large n
    report("byte chi-square", f"{chi:.1f} on 255 dof (z = {zc:+.2f})", abs(zc) < 4)

    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / n
    cov = sum((d[i] - mean) * (d[i + 1] - mean) for i in range(n - 1)) / (n - 1)
    r = cov / var if var else 1.0
    zs = r * math.sqrt(n)
    report("serial correlation", f"{r:+.5f} (z = {zs:+.2f})", abs(zs) < 4)

    ratio = len(zlib.compress(d, 9)) / n
    report("compressibility", f"zlib output {ratio:.4f} of the input", ratio > 0.99)

    blocks = [d[i:i + 16] for i in range(0, n - 15, 16)]
    dupes = len(blocks) - len(set(blocks))
    report("repeated 16-byte blocks", str(dupes), dupes == 0)

    print("all passed" if ok else "SOME TESTS FAILED")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--calls", type=int, default=16)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--trezorctl", default="trezorctl")
    a = sub.add_parser("analyze")
    a.add_argument("path", type=Path)
    args = ap.parse_args()
    if args.cmd == "collect":
        collect(args.trezorctl, args.calls, args.out)
    else:
        sys.exit(0 if analyze(args.path) else 1)


if __name__ == "__main__":
    main()
