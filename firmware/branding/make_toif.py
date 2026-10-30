#!/usr/bin/env python3
"""Ambire flame as the firmware's logo icon (TOIF, 4-bit greyscale "TOIG").

    firmware/branding/make_toif.py SYMBOL.svg OUT.toif [--width 70 --height 102]

SYMBOL.svg is the monochrome flame from AmbireTech/ambire-brand
(Logos/Ambire/SVG/Ambire_symbol_white.svg). It is drawn at the icon's height and
centred on a canvas the size of the Trezor logo it replaces; the alpha channel is
the intensity (the UI paints the icon in its foreground colour). Needs
rsvg-convert and ImageMagick. The format follows trezorlib's toif.py: header
"TOIG", width, height (u16), data length (u32), then raw deflate (wbits -10) of
two pixels per byte, the left one in the high nibble.
"""
import argparse
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--width", type=int, default=70)
    ap.add_argument("--height", type=int, default=102)
    a = ap.parse_args()
    if a.width % 2:
        sys.exit("error: TOIG packs two pixels per byte: the width must be even")
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "flame.png"
        subprocess.run(["rsvg-convert", "-h", str(a.height), "-o", str(png), str(a.svg)], check=True)
        gray = subprocess.run(
            ["magick", str(png), "-background", "none", "-gravity", "center",
             "-extent", f"{a.width}x{a.height}", "-alpha", "extract", "-depth", "8", "gray:-"],
            check=True, capture_output=True).stdout
    if len(gray) != a.width * a.height:
        sys.exit(f"error: got {len(gray)} pixels, want {a.width * a.height}")
    packed = bytes((gray[i] & 0xF0) | (gray[i + 1] >> 4) for i in range(0, len(gray), 2))
    z = zlib.compressobj(level=9, wbits=-10)
    data = z.compress(packed) + z.flush()
    a.out.write_bytes(b"TOIG" + struct.pack("<HHI", a.width, a.height, len(data)) + data)
    lit = sum(1 for p in gray if p > 127)
    print(f"{a.out}: {a.width}x{a.height}, {len(data)} bytes compressed, {lit} pixels lit")


if __name__ == "__main__":
    main()
