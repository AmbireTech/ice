#!/usr/bin/env python3
"""Pulls the near-miss and decoded frames the ESP32-CAM QR scanner kept in
flash, saves them as PGM with their scan results, and decodes each again with
the host build of the decoder (../qrdecode).

    ~/.platformio/penv/bin/python pull_frames.py [--port /dev/cu.usbserial-1120] [--erase]

Frames go to ../../../out/esp32qr/<time>/. Opening the port restarts the
board (the frames are in flash, so they survive); --erase clears them after a
good pull.
"""

import argparse
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent
QRDECODE = HERE.parent / "qrdecode" / "build" / "qrdecode"
BAUD = 460800
HEADER = struct.Struct("<IIHHhBBBBBBHH8x")  # frame_header_t in src/main.c
ERRORS = ["ok", "grid size", "version", "format ECC", "data ECC", "data type",
          "data overflow", "data underflow"]


def open_board(port):
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.5
    s.dtr = s.rts = False  # they drive EN and IO0 on the ESP32-CAM-MB
    s.open()
    boot, t = b"", time.monotonic()
    while b"scanning:" not in boot and time.monotonic() - t < 15:
        boot += s.read(4096)
    if b"scanning:" not in boot:
        print("the scanner did not report 'scanning:' after the restart; trying anyway",
              file=sys.stderr)
    s.timeout = 3
    s.reset_input_buffer()
    s.write(f"B {BAUD}\n".encode())
    reply = s.readline().strip()
    # A status line may come before the OK
    for _ in range(5):
        if reply == b"OK":
            break
        reply = s.readline().strip()
    if reply != b"OK":
        sys.exit(f"baud change not acknowledged (got {reply!r})")
    s.baudrate = BAUD
    s.timeout = 10  # one value for the session: changing it drops bytes on macOS
    time.sleep(0.05)
    s.reset_input_buffer()
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/cu.usbserial-1120")
    ap.add_argument("--erase", action="store_true")
    a = ap.parse_args()

    if not QRDECODE.is_file():
        r = subprocess.run([str(HERE.parent / "qrdecode" / "build.sh")], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"building the decoder failed:\n{r.stderr}")

    out = HERE.parents[2] / "out" / "esp32qr" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    s = open_board(a.port)
    s.write(b"D\n")
    saved = 0
    while True:
        tag = s.read(4)
        if tag == b"DONE":
            print(f"board sent {s.readline().decode().strip()} frame(s)")
            break
        if tag != b"FRM2":
            sys.exit(f"out of step: {tag!r}{s.read(40)!r}")
        raw_h = s.read(HEADER.size)
        (magic, seq, w, h, px_x10, err, ver, decoded, thr, agc, grids, aec, dms) = HEADER.unpack(raw_h)
        px = s.read(w * h)
        tail = s.read(8)
        crc_ok = (len(px) == w * h and tail[:4] == b"END1"
                  and struct.unpack("<I", tail[4:8])[0] == zlib.crc32(px, zlib.crc32(raw_h)))
        if not crc_ok:
            print(f"frame {seq}: corrupted in transfer, skipped", file=sys.stderr)
            continue
        kind = "ok" if decoded else "miss"
        name = f"{seq:05d}_{kind}_{px_x10 // 10}.{px_x10 % 10}ppm"
        path = out / f"{name}.pgm"
        path.write_bytes(f"P5\n{w} {h}\n255\n".encode() + px)
        on_board = (f"decoded v{ver}" if decoded else
                    f"not decoded: {ERRORS[err] if err < len(ERRORS) else err}, {grids} code(s)")
        host = subprocess.run([str(QRDECODE), str(path)], capture_output=True, text=True)
        verdict = host.stdout.strip().split("  ", 1)[-1] if host.stdout else host.stderr.strip()
        line = (f"{name}: board {on_board}, {px_x10 / 10:.1f} px/module, threshold {thr}, "
                f"exposure {aec} gain {agc}, {dms} ms | host: {verdict}")
        print(line)
        with (out / "frames.txt").open("a") as f:
            f.write(line + "\n")
        saved += 1
    print(f"{saved} frame(s) in {out}")
    if a.erase and saved:
        s.write(b"E\n")
        print(f"erase: {s.readline().decode().strip()}")


if __name__ == "__main__":
    main()
