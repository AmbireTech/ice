#!/usr/bin/env python3
"""Live view of the ESP32-CAM reference over its USB-serial cable, every frame
decoded with the device's own QR decoder (../qrdecode, built from the
firmware's sources).

    ~/.platformio/penv/bin/python serial_view.py [--port /dev/cu.usbserial-1120]
        [--set field=1 --set framesize=…] [--http 8766]

Open http://localhost:8766/ on this Mac: the latest frame, 1:1, and the
decoder's verdict. Frames (PGM + PNG) and results.txt go to
../../../out/esp32frames/<time>/; every decoded frame is kept, and one
undecoded frame a second.
Needs pyserial (PlatformIO's Python has it).
"""

import argparse
import http.server
import json
import struct
import subprocess
import zlib as _zlib
import sys
import threading
import time
import zlib
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent
QRDECODE = HERE.parent / "qrdecode" / "build" / "qrdecode"
BOOT_BAUD = 115200
# The fastest rate the ESP32-CAM-MB's CH340 carries frames without loss here
# (1.5 Mbaud drops bytes, 921600 does not connect)
BAUD = 460800

PAGE = """<!doctype html><meta charset=utf-8><title>OV2640 reference</title>
<style>body{background:#111;color:#ddd;font:14px monospace;margin:16px}
img{image-rendering:pixelated;border:1px solid #444;width:min(96vw,640px)}
#r{font-size:18px;margin:8px 0}.ok{color:#7f7}.no{color:#f77}</style>
<div id=r>waiting</div><img id=i><div id=d></div>
<script>
async function tick(){
 try{const s=await (await fetch('state.json?'+Date.now())).json();
  const r=document.getElementById('r');
  r.textContent=s.line; r.className=s.ok?'ok':'no';
  document.getElementById('d').textContent=s.detail;
  document.getElementById('i').src='latest.png?'+s.seq;}
 catch(e){document.getElementById('r').textContent='viewer not answering: '+e;}
 setTimeout(tick,250);}
tick();
</script>"""


def png_gray(w, h, px):
    raw = b"".join(b"\x00" + px[y * w:(y + 1) * w] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


class Esp:
    def __init__(self, port):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, BOOT_BAUD, 0.5
        # RTS/DTR released: on the ESP32-CAM-MB they drive EN and IO0
        self.s.dtr = self.s.rts = False
        self.s.open()
        # Opening the port resets the board: wait for its boot
        boot, t = b"", time.monotonic()
        while b"serial ready" not in boot and time.monotonic() - t < 10:
            boot += self.s.read(4096)
        if b"serial ready" not in boot:
            print("no 'serial ready' from the ESP32 (already running?), trying anyway", file=sys.stderr)
        self.s.timeout = 3
        self.s.reset_input_buffer()
        reply = self.line(f"B {BAUD}")
        if reply != "OK":
            raise RuntimeError(f"baud change refused: {reply}")
        self.s.baudrate = BAUD
        # One timeout for the session, long enough for a whole frame: changing
        # it reconfigures the port, which on macOS drops bytes in flight
        self.s.timeout = 8
        time.sleep(0.05)
        self.s.reset_input_buffer()

    def line(self, cmd):
        self.s.write(cmd.encode() + b"\n")
        reply = self.s.readline().decode(errors="replace").strip()
        if not reply:
            raise RuntimeError(f"no reply to {cmd!r}")
        return reply

    def frame(self, skip=0):
        self.s.write(f"G {skip}\n".encode())
        head = self.s.read(4)
        if head == b"ERR ":
            raise RuntimeError("ESP32: " + self.s.readline().decode(errors="replace").strip())
        if head != b"FRM1":
            junk = head + self.s.read(64)
            self.s.reset_input_buffer()
            raise RuntimeError(f"out of step (got {junk[:32]!r})")
        w, h, n = struct.unpack("<HHI", self.s.read(8))
        px = self.s.read(n)
        tail = self.s.read(8)
        if len(px) != n or tail[:4] != b"END1" or struct.unpack("<I", tail[4:8])[0] != _zlib.crc32(px):
            time.sleep(0.5)
            self.s.reset_input_buffer()
            raise RuntimeError(f"bad frame: {len(px)} of {n} bytes, tail {tail[:4]!r} (CRC checked)")
        return w, h, px


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/cu.usbserial-1120")
    ap.add_argument("--set", action="append", default=[], metavar="VAR=VAL")
    ap.add_argument("--http", type=int, default=8766)
    a = ap.parse_args()

    if not QRDECODE.is_file():
        build = subprocess.run([str(HERE.parent / "qrdecode" / "build.sh")], capture_output=True, text=True)
        if build.returncode != 0:
            sys.exit(f"building the decoder failed:\n{build.stderr}")

    out = HERE.parents[2] / "out" / "esp32frames" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    web = out / "web"
    web.mkdir()
    (web / "index.html").write_text(PAGE)
    (web / "state.json").write_text(json.dumps({"line": "starting", "ok": False, "detail": "", "seq": 0}))

    handler = lambda *args: http.server.SimpleHTTPRequestHandler(*args, directory=str(web))  # noqa: E731

    class Quiet(http.server.ThreadingHTTPServer):
        pass

    srv = Quiet(("127.0.0.1", a.http), handler)
    srv.RequestHandlerClass.log_message = lambda *args: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"open http://localhost:{a.http}/   frames in {out}")

    esp = Esp(a.port)
    for kv in a.set:
        var, _, val = kv.partition("=")
        print(f"S {var} {val}: {esp.line(f'S {var} {val}')}")
    print(esp.line("Q"))

    results = (out / "results.txt").open("a")
    seq, ok_count, last_kept = 0, 0, 0.0
    t_prev = time.monotonic()
    while True:
        try:
            w, h, px = esp.frame()
        except (RuntimeError, serial.SerialException) as e:
            print(f"frame failed: {e}", file=sys.stderr)
            if "camera not running" in str(e):
                # The ESP32-CAM sometimes misses the sensor at boot: start it again
                try:
                    print(f"restarting the camera: {esp.line('S defaults')}", file=sys.stderr)
                except RuntimeError as e2:
                    print(f"camera restart failed: {e2}", file=sys.stderr)
            time.sleep(0.5)
            continue
        seq += 1
        now = time.monotonic()
        fps = 1.0 / max(now - t_prev, 1e-3)
        t_prev = now
        pgm = web / "latest.pgm"
        pgm.write_bytes(f"P5\n{w} {h}\n255\n".encode() + px)
        (web / "latest.png").write_bytes(png_gray(w, h, px))
        dec = subprocess.run([str(QRDECODE), str(pgm)], capture_output=True, text=True)
        verdict = (dec.stdout.strip() or dec.stderr.strip()).split("  ", 1)[-1]
        ok = "  OK  " in dec.stdout
        ok_count += ok
        line = f"#{seq} {w}x{h} {fps:.1f} fps  {verdict}"
        print(line)
        results.write(line + "\n")
        results.flush()
        if ok or now - last_kept >= 1.0:
            name = f"{seq:05d}_{'ok' if ok else 'no'}"
            (out / f"{name}.pgm").write_bytes(pgm.read_bytes())
            last_kept = now
        (web / "state.json").write_text(json.dumps({
            "line": ("DECODED " if ok else "no decode ") + verdict,
            "ok": ok, "seq": seq,
            "detail": f"frame {seq}, {w}x{h}, {fps:.1f} fps, decoded {ok_count} of {seq}"}))


if __name__ == "__main__":
    main()
