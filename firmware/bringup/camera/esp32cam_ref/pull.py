#!/usr/bin/env python3
"""Pull raw frames from the ESP32-CAM OV2640 reference (stdlib only).

Join the ESP32's Wi-Fi (ov2640-ref) first. Each frame is saved as PGM (the
exact Y bytes) and PNG; the camera's status (mode, window, settings, live
exposure and gain) goes beside them as status.json.

    pull.py                                  # 5 frames at the current setting
    pull.py -n 10 -o frames/native320 --set framesize=7 --set field=3
    pull.py --set format=0 --fmt jpeg        # JPEG frames as the sensor sent them

--set VAR=VAL is the page's /set call: framesize (enum number, see
/status "framesizes"), field (0 full, 1 wide, 2 half, 3 native), format
(1 gray, 0 jpeg), xclk (10/20), or any sensor setting (aec, aec_value, agc,
agc_gain, gainceiling, ae_level, brightness, contrast, ...). They are applied
in order, then --skip frames are dropped so exposure can settle.
"""

import argparse
import json
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path


def die(msg):
    print(f"pull: {msg}", file=sys.stderr)
    sys.exit(1)


def get(base, path, timeout):
    url = base + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        die(f"{url}: HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        die(f"{url}: {e.reason} (joined the ov2640-ref Wi-Fi?)")


def parse_pgm(data):
    # "P5\n<w> <h>\n255\n" then w*h bytes
    fields, p = [], 0
    while len(fields) < 4:
        while data[p:p + 1].isspace():
            p += 1
        start = p
        while not data[p:p + 1].isspace():
            p += 1
        fields.append(data[start:p])
    p += 1
    if fields[0] != b"P5" or fields[3] != b"255":
        die(f"not an 8-bit PGM: header {fields}")
    w, h = int(fields[1]), int(fields[2])
    px = data[p:]
    if len(px) != w * h:
        die(f"PGM {w}x{h} carries {len(px)} bytes, {w * h} expected")
    return w, h, px


def png_gray(path, w, h, pixels):
    raw = b"".join(b"\x00" + pixels[y * w:(y + 1) * w] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def stats(px):
    # the same summary camdbg.py prints for our device's frames
    n = len(px)
    hist = [0] * 256
    for v in px:
        hist[v] += 1
    mean = sum(i * c for i, c in enumerate(hist)) / n
    clip_hi = sum(hist[250:]) * 100 / n
    clip_lo = sum(hist[:6]) * 100 / n
    acc, p = 0, {}
    for i, c in enumerate(hist):
        acc += c
        for q in (1, 50, 99):
            if q not in p and acc >= n * q / 100:
                p[q] = i
    return f"mean {mean:.0f}  p1/p50/p99 {p[1]}/{p[50]}/{p[99]}  clip>=250 {clip_hi:.1f}%  <=5 {clip_lo:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=5, help="frames to save (default 5)")
    ap.add_argument("-o", "--out", default="frames", help="output directory (default ./frames)")
    ap.add_argument("--host", default="192.168.4.1")
    ap.add_argument("--set", action="append", default=[], metavar="VAR=VAL", help="apply before pulling (repeatable)")
    ap.add_argument("--skip", type=int, default=3, help="frames dropped before the first saved one (default 3)")
    ap.add_argument("--fmt", choices=("y8", "jpeg"), default="y8")
    ap.add_argument("--timeout", type=float, default=30, help="seconds per request (UXGA Y8 is ~2 MB)")
    a = ap.parse_args()

    base = f"http://{a.host}"
    for s in a.set:
        var, sep, val = s.partition("=")
        if not sep:
            die(f"--set {s}: need VAR=VAL")
        get(base, "/set?" + urllib.parse.urlencode({"var": var, "val": val}), a.timeout)
        print(f"set {var}={val}")

    body, _ = get(base, "/status", a.timeout)
    status = json.loads(body)
    if status.get("error"):
        print(f"pull: camera reports: {status['error']}", file=sys.stderr)
    m, w = status["mode"], status["window"]
    print(f"{m['width']}x{m['height']} {'Y8' if m['format'] else 'JPEG'}, field {m['field_name']}"
          + ("" if w["sensor_mode"] == "driver" else f" ({w['sensor_mode']} window {w['mx']}x{w['my']} at {w['ox']},{w['oy']})")
          + f", XCLK {m['xclk']} MHz")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps(status, indent=1) + "\n")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i in range(a.n):
        skip = a.skip if i == 0 else 0
        data, hdr = get(base, f"/raw?fmt={a.fmt}&skip={skip}", a.timeout)
        name = out / f"{stamp}_{i:02d}"
        if a.fmt == "jpeg":
            name = name.with_suffix(".jpg")
            name.write_bytes(data)
            print(f"{name}  {len(data)} bytes  {hdr.get('X-Width')}x{hdr.get('X-Height')}")
            continue
        fw, fh, px = parse_pgm(data)
        name.with_suffix(".pgm").write_bytes(data)
        png_gray(name.with_suffix(".png"), fw, fh, px)
        print(f"{name}.pgm/.png  {fw}x{fh}  {stats(px)}")


if __name__ == "__main__":
    main()
